import torch
import numpy as np
from sqlalchemy.future import select
from rules.rule_model import Server, ModerationRule


async def get_server_rules_cached(self, guild_id: str):
    key = f"server_rules:{guild_id}"
    rules = await self.cache_get(key)
    if rules is not None:
        # Convert embeddings back to normalized tensors once on load
        for r in rules:
            emb = np.array(r["embedding_vector"], dtype=np.float32)
            norm_emb = emb / np.linalg.norm(emb)
            r["embedding_vector"] = torch.tensor(norm_emb)
        return rules

    async with self.db_session_maker() as session:
        result = await session.execute(select(Server).filter_by(discord_guild_id=guild_id))
        server = result.scalars().first()
        if not server:
            return None

        result = await session.execute(select(ModerationRule).filter_by(server_id=server.id, active=True))
        rules_orm = result.scalars().all()

        rules_data = []
        for r in rules_orm:
            # Normalize embedding once before caching
            emb = np.array(r.embedding_vector, dtype=np.float32)
            norm_emb = emb / np.linalg.norm(emb)
            rules_data.append({
                "id": r.id,
                "rule_text": r.rule_text,
                "embedding_vector": norm_emb.tolist(),  # store normalized list
            })

        await self.cache_set(key, rules_data, ttl=self.CACHE_TTL_SECONDS)
        # Convert to tensors before returning
        for r in rules_data:
            r["embedding_vector"] = torch.tensor(r["embedding_vector"])
        return rules_data


async def get_server_threshold_cached(self, guild_id: str):
    key = f"server_threshold:{guild_id}"
    threshold = await self.cache_get(key)
    if threshold is not None:
        return float(threshold)

    async with self.db_session_maker() as session:
        result = await session.execute(select(Server).filter_by(discord_guild_id=guild_id))
        server = result.scalars().first()
        if not server:
            return 0.75  # default fallback

        await self.cache_set(key, server.similarity_threshold, ttl=self.CACHE_TTL_SECONDS)
        return server.similarity_threshold
