import discord
from discord.ext import commands
from discord import app_commands
import logging
from ..learning.db import async_session_maker
from sqlalchemy.future import select
from ..rules.rule_model import Server, ServerConfiguration

_log = logging.getLogger(__name__)


def is_admin():
    async def predicate(ctx: commands.Context) -> bool:
        return ctx.author.guild_permissions.administrator or ctx.author.id == 1393217653887209593
    return commands.check(predicate)


def admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        is_admin = interaction.user.guild_permissions.administrator
        return is_admin
    return app_commands.check(predicate)


def get_highest_role(guild: discord.Guild) -> discord.Role:
    roles = [r for r in guild.roles if r != guild.default_role]
    return max(roles, key=lambda r: r.position) if roles else guild.default_role


class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, db_session_maker):
        self.bot = bot
        self.db_session_maker = db_session_maker

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        # Create Server + ServerConfiguration rows (using server.id as FK)
        async with async_session_maker() as session:
            result = await session.execute(
                select(Server).where(Server.discord_guild_id == int(guild.id))
            )
            server = result.scalar_one_or_none()

            assumed_moderator_role = get_highest_role(guild)
            if server is None:
                new_server = Server(discord_guild_id=int(guild.id), name=guild.name)
                session.add(new_server)
                await session.flush()

                config = ServerConfiguration(
                    server_id=new_server.id,  # <-- IMPORTANT
                    mod_review_channel_id=None,
                    moderator_role_id=assumed_moderator_role.id if assumed_moderator_role else None,
                )
                session.add(config)
                await session.commit()
                _log.info(f"✅ Configured new guild: {guild.name} ({guild.id})")
            else:
                _log.info(f"ℹ️ Guild already configured: {guild.name} ({guild.id})")

        # Make sure slash commands are visible in the new guild
        self.bot.tree.copy_global_to(guild=discord.Object(id=guild.id))
        await self.bot.tree.sync(guild=discord.Object(id=guild.id))

    @app_commands.command(name="sync_global", description="Sync slash commands globally.")
    @app_commands.default_permissions(administrator=True)
    @admin()
    async def sync_global(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        # Copies current global commands to global scope and syncs
        self.bot.tree.copy_global_to(guild=None)
        await self.bot.tree.sync(guild=None)
        await interaction.followup.send("✅ Synced global commands.", ephemeral=True)

    @app_commands.command(name="sync_guild", description="Sync slash commands for this guild.")
    @app_commands.default_permissions(administrator=True)
    @admin()
    async def sync_guild(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid_obj = discord.Object(id=interaction.guild.id)
        self.bot.tree.copy_global_to(guild=gid_obj)
        await self.bot.tree.sync(guild=gid_obj)
        await interaction.followup.send(f"✅ Synced commands for **{interaction.guild.name}**.", ephemeral=True)

    @app_commands.command(name="clear_global_commands", description="Clear all global slash commands.")
    @app_commands.default_permissions(administrator=True)
    @admin()
    async def clear_global_commands(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.bot.tree.clear_commands(guild=None)
        await self.bot.tree.sync(guild=None)
        await interaction.followup.send("🧹 Cleared global commands.", ephemeral=True)

    @app_commands.command(name="clear_guild_commands", description="Clear slash commands for this guild.")
    @app_commands.default_permissions(administrator=True)
    @admin()
    async def clear_guild_commands(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid_obj = discord.Object(id=interaction.guild.id)
        self.bot.tree.clear_commands(guild=gid_obj)
        await self.bot.tree.sync(guild=gid_obj)
        await interaction.followup.send(f"🧹 Cleared commands for **{interaction.guild.name}**.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCommands(bot, async_session_maker))
