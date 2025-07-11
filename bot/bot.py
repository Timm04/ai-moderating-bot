import os
import discord
import logging
import sys
import traceback
from discord.ext import commands

_log = logging.getLogger(__name__)


class AMABot(commands.Bot):
    def __init__(self, command_prefix,
                 cogs_folder,
                 events_folder,
                 moderation_folder,
                 rules_folder,
                 learning_folder):

        super().__init__(command_prefix=command_prefix,
                         intents=discord.Intents.all())
        self.cogs_folder = cogs_folder
        self.events_folder = events_folder
        self.moderation_folder = moderation_folder

    async def on_ready(self):
        _log.info(f"Logged in as {self.user}")
        await self.create_debug_dm()

    async def setup_hook(self):
        self.tree.on_error = self.on_application_command_error

    async def load_cogs(self, cogs_to_load):

        cogs = [cog for cog in os.listdir(self.cogs_folder)
                if cog.endswith(".py")
                and (cogs_to_load == "*" or cog[:-3] in cogs_to_load)]

        for cog in cogs:
            cog = f"{self.cog_folder}.{cog[:-3]}"
            await self.load_extension(cog)
            print(f"Loaded {cog}")

    async def create_debug_dm(self):
        await self.wait_until_ready()
        debug_user_id = int(os.getenv("DEBUG_USER"))
        debug_user = self.get_user(debug_user_id)
        if not debug_user:
            debug_user = await self.fetch_user(debug_user_id)

        self.debug_dm = debug_user.dm_channel
        if not debug_user.dm_channel:
            self.debug_dm = await debug_user.create_dm()

        await self.debug_dm.send("Bot is ready.")

    async def on_command_error(self, ctx: commands.Context,
                               error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            _log.info(f"Command by user {ctx.author.name} not found: {ctx.message.content}")
            return

        raise error

    # TODO: Implement on application command error handling

    async def on_error(self, event_method, *args, **kwargs):
        _log.exception('Ignoring exception in %s', event_method)

        error_type, error, tb = sys.exc_info()

        traceback_string = '\n'.join(traceback.format_list(traceback.extract_tb(tb)))

        error_message = f"`{error_type}` occurred in `{event_method}`\n" + \
            f"```{error}```"
        embed_description = f"\n```python\n{traceback_string}```"

        error_embed = discord.Embed(title="Error",
                                    description=embed_description[:4000],
                                    color=discord.Color.red())
        await self.debug_dm.send(error_message, embed=error_embed)
