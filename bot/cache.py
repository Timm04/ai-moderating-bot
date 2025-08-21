from sqlalchemy.future import select
from .rules.rule_model import Server, ModerationRule
import torch
import json
import numpy as np


class Cache:
    def __init__(self, redis_client, db_session_maker, prefix="msgmon"):
        self.redis = redis_client
        self.db_session_maker = db_session_maker
        self.prefix = prefix
        self.CACHE_TTL_SECONDS = 600

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    async def get(self, key: str):
        raw = await self.redis.get(self._key(key))
        return None if raw is None else json.loads(raw)

    async def set(self, key: str, value, ttl: int):
        await self.redis.set(self._key(key), json.dumps(value), ex=ttl)

    async def get_server_rules_cached(self, guild_id: int):
        key = f"server_rules:{guild_id}"
        rules = await self.get(key)
        if rules is not None:
            for r in rules:
                emb = np.array(r["embedding_vector"], dtype=np.float64)
                r["embedding_vector"] = torch.tensor(emb / np.linalg.norm(emb))
            return rules

        async with self.db_session_maker() as session:
            server = (await session.execute(
                select(Server).where(Server.discord_guild_id == int(guild_id))
            )).scalar_one_or_none()
            if not server:
                return []

            rules_orm = (await session.execute(
                select(ModerationRule).where(ModerationRule.server_id == server.id,
                                             ModerationRule.active.is_(True))
            )).scalars().all()

            payload = []
            for r in rules_orm:
                emb = np.array(r.embedding_vector, dtype=np.float64)
                payload.append({
                    "id": r.id,
                    "rule_text": r.rule_text,
                    "embedding_vector": (emb / np.linalg.norm(emb)).tolist(),
                })
            await self.set(key, payload, ttl=self.CACHE_TTL_SECONDS)
            for r in payload:
                r["embedding_vector"] = torch.tensor(r["embedding_vector"])
            return payload

    async def get_server_threshold_cached(self, guild_id: int):
        key = f"server_threshold:{guild_id}"
        data = await self.get(key)
        if data is not None:
            return float(data)

        async with self.db_session_maker() as session:
            server = (await session.execute(
                select(Server).where(Server.discord_guild_id == int(guild_id))
            )).scalar_one_or_none()
            if not server or not server.configuration:
                return 0.75
            val = server.configuration.similarity_threshold
            await self.set(key, val, ttl=self.CACHE_TTL_SECONDS)
            return val