import os
import argparse
import asyncio
from dotenv import load_dotenv
import discord
from bot.bot import AMABot


load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX")
COGS_FOLDER = "cogs"
EVENTS_FOLDER = "events"
MODERATION_FOLDER = "moderation"
RULES_FOLDER = "rules"
LEARNING_FOLDER = "learning"


bot = AMABot(command_prefix=COMMAND_PREFIX,
             cogs_folder=COGS_FOLDER,
             events_folder=EVENTS_FOLDER,
             moderation_folder=MODERATION_FOLDER,
             rules_folder=RULES_FOLDER,
             learning_folder=LEARNING_FOLDER)


async def main(cogs_to_load):
    discord.utils.setup_logging()
    await bot.load_cogs(cogs_to_load)
    await bot.start(TOKEN)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Moderation Bot")
    parser.add_argument("cogs", nargs="*", help="List of cogs to load, without the .py extension")
    args = parser.parse_args()

    cogs_to_load = args.cogs if args.cogs else "*"

    asyncio.run(main(cogs_to_load))
