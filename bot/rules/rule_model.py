from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, Float, BigInteger
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy import UniqueConstraint
from datetime import datetime

Base = declarative_base()


class Server(Base):
    __tablename__ = "servers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_guild_id = Column(BigInteger, unique=True, nullable=False)
    name = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    rules = relationship("ModerationRule", back_populates="server", cascade="all, delete-orphan")
    configuration = relationship("ServerConfiguration", back_populates="server", uselist=False, cascade="all, delete-orphan")


class ServerConfiguration(Base):
    __tablename__ = "server_configurations"
    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False, unique=True)  # <- FK to servers.id
    mod_review_channel_id = Column(BigInteger, nullable=True)
    moderator_role_id = Column(BigInteger, nullable=True)

    similarity_threshold = Column(Float, default=0.75)
    vote_duration_minutes = Column(Integer, default=1440)
    majority_required = Column(Float, default=0.75)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    server = relationship("Server", back_populates="configuration")


class ModerationRule(Base):
    __tablename__ = "moderation_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    rule_text = Column(Text, nullable=False)
    embedding_vector = Column(JSON, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    server = relationship("Server", back_populates="rules")
    flagged_messages = relationship("FlaggedMessage", back_populates="rule", cascade="all, delete-orphan")


class FlaggedMessage(Base):
    __tablename__ = "flagged_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(BigInteger, nullable=False, index=True)
    rule_id = Column(Integer, ForeignKey("moderation_rules.id"), nullable=False, index=True)
    approved = Column(Boolean, nullable=True)  # None = pending
    moderator_id = Column(BigInteger, nullable=True)
    similarity = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    message_excerpt = Column(Text, nullable=True)

    # <- THIS must be named exactly "rule" to match back_populates="rule" above
    rule = relationship("ModerationRule", back_populates="flagged_messages")


class FlaggedMessageVote(Base):
    __tablename__ = "flagged_message_votes"
    id = Column(Integer, primary_key=True)
    flagged_message_id = Column(Integer, ForeignKey("flagged_messages.id"), nullable=False)  # Integer FK
    moderator_id = Column(BigInteger, nullable=False)
    vote = Column(Boolean, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("flagged_message_id", "moderator_id", name="unique_vote_per_mod"),
    )
