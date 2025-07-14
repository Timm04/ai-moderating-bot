import asyncio
import asyncpg


async def test():
    conn = await asyncpg.connect(
        user='timm2',
        password='dein_passwort',
        database='ai_bot_db',
        host='localhost',
        port=5431
    )
    print("Connected successfully!")
    await conn.close()

asyncio.run(test())
