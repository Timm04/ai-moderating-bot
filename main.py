import os
import argparse
import asyncio
from dotenv import load_dotenv
import discord
from bot.bot import AMABot

from bot.learning.db import create_tables
from bot.learning import async_session_maker


load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX")
COGS_FOLDER = "bot/cogs"
EVENTS_FOLDER = "bot/events"
MODERATION_FOLDER = "bot/moderation"
RULES_FOLDER = "bot/rules"
LEARNING_FOLDER = "bot/learning"

bot = AMABot(command_prefix=COMMAND_PREFIX,
             cogs_folder=COGS_FOLDER,
             events_folder=EVENTS_FOLDER,
             moderation_folder=MODERATION_FOLDER,
             rules_folder=RULES_FOLDER,
             learning_folder=LEARNING_FOLDER,
             db_session_maker=async_session_maker)


async def main(cogs_to_load):
    discord.utils.setup_logging()
    await bot.load_cogs(cogs_to_load)
    await bot.start(TOKEN)
    await create_tables()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Moderation Bot")
    parser.add_argument("cogs", nargs="*", help="List of cogs to load, without the .py extension")
    args = parser.parse_args()

    cogs_to_load = args.cogs if args.cogs else "*"

    asyncio.run(main(cogs_to_load))
