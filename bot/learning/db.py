import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from ..rules.rule_model import Server, ServerConfiguration
from sqlalchemy.future import select
from bot.rules.rule_model import Base

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in the environment.")

engine = create_async_engine(DATABASE_URL, echo=True)

async_session_maker = sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def create_server_configurations():
    async with async_session_maker() as session:
        servers = (await session.execute(select(Server))).scalars().all()
        for server in servers:
            if not server.configuration:
                config = ServerConfiguration(server_id=server.id, similarity_threshold=0.7)
                session.add(config)
        await session.commit()


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
