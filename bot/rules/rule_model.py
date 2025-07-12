from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, Float
)
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, timezone

Base = declarative_base()


class Server(Base):
    __tablename__ = "servers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_guild_id = Column(String(32), unique=True, nullable=False)
    name = Column(String(100))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    rules = relationship("ModerationRule", back_populates="server", cascade="all, delete-orphan")
    similarity_threshold = Column(Float, nullable=False, default=0.75)


class ModerationRule(Base):
    __tablename__ = "moderation_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    rule_text = Column(Text, nullable=False)
    embedding_vector = Column(JSON, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    rule_metadata = Column(JSON, nullable=True)

    server = relationship("Server", back_populates="rules")
    flagged_messages = relationship("FlaggedMessage", back_populates="rule", cascade="all, delete-orphan")


class FlaggedMessage(Base):
    __tablename__ = "flagged_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(64), nullable=False, index=True)  # Discord message ID
    rule_id = Column(Integer, ForeignKey("moderation_rules.id"), nullable=False)
    approved = Column(Boolean, nullable=False)
    moderator_id = Column(String(64), nullable=False)  # Discord user ID of the mod
    similarity = Column(Float, nullable=True)  # similarity score when flagged
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    rule = relationship("ModerationRule", back_populates="flagged_messages")
