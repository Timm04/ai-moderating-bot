import discord
from discord.ext import commands


def is_admin():
    async def predicate(ctx: commands.Context) -> bool:
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)


class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command()
    @is_admin()
    async def sync_global(self, interaction: discord.Interaction):
        self.bot.tree.copy_global_to(guild=None)
        self.bot.tree.sync(guild=None)
        await self.bot.tree.sync(guild=None)
        await interaction.response.send_message("Synced global commands.", ephemeral=True)

    @commands.command()
    @is_admin()
    async def sync_guild(self, interaction: discord.Interaction):
        self.bot.tree.copy_global_to(guild=discord.Object(id=interaction.guild.id))
        self.bot.tree.sync(guild=None)
        await self.bot.tree.sync(guild=discord.Object(id=interaction.guild.id))
        await interaction.response.send_message(f"Synced commands for {interaction.guild.name}.", ephemeral=True)

    @commands.command()
    @is_admin()
    async def clear_global_commands(self, interaction: discord.Interaction):
        self.bot.tree.clear_commands(guild=None)
        await self.bot.tree.sync(guild=None)
        await interaction.response.send_message("Cleared global commands.", ephemeral=True)

    @commands.command()
    @is_admin()
    async def clear_guild_commands(self, interaction: discord.Interaction):
        self.bot.tree.clear_commands(guild=discord.Object(id=interaction.guild.id))
        await self.bot.tree.sync(guild=discord.Object(id=interaction.guild.id))
        await interaction.response.send_message(f"Cleared commands for {interaction.guild.name}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCommands(bot))
