import types
import discord
from discord.ext import commands
from discord import app_commands
from sqlalchemy.future import select

from ..learning.db import async_session_maker
from ..rules.rule_model import Server, ServerConfiguration


def _overview_lines(cfg: "types.SimpleNamespace") -> str:
    lines = ["**Overview**"]
    if cfg.mod_review_channel_id:
        lines.append(f"**Mod Review Channel**: <#{cfg.mod_review_channel_id}>")
    else:
        lines.append("**Mod Review Channel**: Not set")

    lines.append(f"**Threshold**: {cfg.similarity_threshold:.2f}")
    lines.append(f"**Vote Timeout**: {cfg.vote_duration_minutes} minutes")

    if cfg.moderator_role_id:
        lines.append(f"**Moderator Role**: <@&{cfg.moderator_role_id}>")
    else:
        lines.append("**Moderator Role**: Not set")

    return "\n".join(lines)


class SetupView(discord.ui.View):
    def __init__(self, guild: discord.Guild, config_snapshot: "types.SimpleNamespace"):
        """
        config_snapshot is a SimpleNamespace with:
          id, server_id, mod_review_channel_id, moderator_role_id,
          similarity_threshold, vote_duration_minutes, majority_required
        """
        super().__init__(timeout=600)
        self.guild = guild
        self.page = 0
        self.config = config_snapshot  # <- snapshot (no lazy loads)
        self.update_buttons()

    def update_buttons(self):
        # base nav / quit controls
        self.clear_items()
        self.add_item(discord.ui.Button(label="≪", style=discord.ButtonStyle.grey, row=1, custom_id="first"))
        self.add_item(discord.ui.Button(label="Back", style=discord.ButtonStyle.blurple, row=1, custom_id="back"))
        self.add_item(discord.ui.Button(label="Next", style=discord.ButtonStyle.blurple, row=1, custom_id="next"))
        self.add_item(discord.ui.Button(label="≫", style=discord.ButtonStyle.grey, row=1, custom_id="last"))
        self.add_item(discord.ui.Button(label="Quit", style=discord.ButtonStyle.red, row=1, custom_id="quit"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # optionally restrict to guild owner/admin here
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    async def update_page(self, interaction: discord.Interaction):
        embed = await self.get_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def get_embed(self):
        embed = discord.Embed(title=f"Server Setup — Page {self.page + 1}")
        if self.page == 0:
            embed.description = _overview_lines(self.config)

        elif self.page == 1:
            embed.description = "**Select Mod Review Channel**"
            self.clear_items()
            self.add_item(ChannelDropdown(self.guild.text_channels, self))
            self.update_buttons()

        elif self.page == 2:
            embed.description = "**Set Threshold (0 to 1)**"
            self.clear_items()
            self.add_item(ThresholdGrid(self))
            self.update_buttons()

        elif self.page == 3:
            embed.description = "**Set Vote Timeout**"
            self.clear_items()
            self.add_item(VoteTimeoutDropdown(self))
            self.update_buttons()

        elif self.page == 4:
            embed.description = "**Select Moderator Role**"
            self.clear_items()
            self.add_item(RoleDropdown(self.guild.roles, self))
            self.update_buttons()

        else:
            embed.description = "Page not implemented yet."

        return embed

    # Nav buttons
    @discord.ui.button(label="≪", style=discord.ButtonStyle.grey, row=1)
    async def first_page(self, interaction: discord.Interaction, _):
        self.page = 0
        await self.update_page(interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.blurple, row=1)
    async def prev_page(self, interaction: discord.Interaction, _):
        self.page = max(self.page - 1, 0)
        await self.update_page(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple, row=1)
    async def next_page(self, interaction: discord.Interaction, _):
        self.page = min(self.page + 1, 4)
        await self.update_page(interaction)

    @discord.ui.button(label="≫", style=discord.ButtonStyle.grey, row=1)
    async def last_page(self, interaction: discord.Interaction, _):
        self.page = 4
        await self.update_page(interaction)

    @discord.ui.button(label="Quit", style=discord.ButtonStyle.red, row=1)
    async def quit(self, interaction: discord.Interaction, _):
        await interaction.message.delete()


class ChannelDropdown(discord.ui.Select):
    def __init__(self, channels, parent_view: SetupView):
        self.parent_view = parent_view
        options = [discord.SelectOption(label=ch.name, value=str(ch.id)) for ch in channels]
        super().__init__(placeholder="Choose a mod-review channel", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        new_channel_id = int(self.values[0])

        async with async_session_maker() as session:
            cfg = await session.get(ServerConfiguration, self.parent_view.config.id)
            if cfg:
                cfg.mod_review_channel_id = new_channel_id
                await session.commit()

        self.parent_view.config.mod_review_channel_id = new_channel_id
        await self.parent_view.update_page(interaction)


class ThresholdGrid(discord.ui.View):
    def __init__(self, parent_view: SetupView):
        super().__init__()
        self.parent_view = parent_view
        self.value = list(str(parent_view.config.similarity_threshold or "0.75"))

        layout = [
            ["0", "1", "2"],
            ["3", "4", "5"],
            ["6", "7", "8"],
            ["9", ".", "Del"],
        ]
        for row in layout:
            for label in row:
                self.add_item(ThresholdButton(label, self))

    async def update_threshold(self, interaction: discord.Interaction):
        # normalize displayed value
        s = "".join(self.value).replace("Del", "")
        try:
            val = float(s)
        except ValueError:
            await self.parent_view.update_page(interaction)
            return

        if 0.0 <= val <= 1.0:
            async with async_session_maker() as session:
                cfg = await session.get(ServerConfiguration, self.parent_view.config.id)
                if cfg:
                    cfg.similarity_threshold = val
                    await session.commit()
            self.parent_view.config.similarity_threshold = val

        await self.parent_view.update_page(interaction)


class ThresholdButton(discord.ui.Button):
    def __init__(self, label: str, grid: ThresholdGrid):
        style = discord.ButtonStyle.secondary
        if label == "Del":
            style = discord.ButtonStyle.danger
        super().__init__(label=label, style=style)
        self.grid = grid

    async def callback(self, interaction: discord.Interaction):
        if self.label == "Del":
            if self.grid.value:
                self.grid.value.pop()
        else:
            self.grid.value.append(self.label)
        await self.grid.update_threshold(interaction)


def get_minutes_from_label(label: str) -> int:
    mapping = {
        "30 mins": 30,
        "1 hour": 60,
        "3 hours": 180,
        "6 hours": 360,
        "12 hours": 720,
        "1 day": 1440,
    }
    return mapping.get(label, 60)


class VoteTimeoutDropdown(discord.ui.Select):
    def __init__(self, parent_view: SetupView):
        self.parent_view = parent_view
        times = ["30 mins", "1 hour", "3 hours", "6 hours", "12 hours", "1 day"]
        options = [discord.SelectOption(label=t, value=t) for t in times]
        super().__init__(placeholder="Choose vote timeout", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        selected_label = self.values[0]
        minutes = get_minutes_from_label(selected_label)

        async with async_session_maker() as session:
            cfg = await session.get(ServerConfiguration, self.parent_view.config.id)
            if cfg:
                cfg.vote_duration_minutes = minutes
                await session.commit()

        self.parent_view.config.vote_duration_minutes = minutes
        await self.parent_view.update_page(interaction)


class RoleDropdown(discord.ui.Select):
    def __init__(self, roles, parent_view: SetupView):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label=role.name, value=str(role.id))
            for role in roles
            if not role.managed and (parent_view.guild.me is None or role < parent_view.guild.me.top_role)
        ]
        super().__init__(placeholder="Select a moderator role", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        new_role_id = int(self.values[0])

        async with async_session_maker() as session:
            cfg = await session.get(ServerConfiguration, self.parent_view.config.id)
            if cfg:
                cfg.moderator_role_id = new_role_id
                await session.commit()

        self.parent_view.config.moderator_role_id = new_role_id
        await self.parent_view.update_page(interaction)


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot, db_session_maker):
        self.bot = bot
        self.db_session_maker = db_session_maker

    @app_commands.command(name="setup", description="Initialize server configuration for moderation.")
    async def setup_guild(self, interaction: discord.Interaction):
        guild = interaction.guild
        guild_id = int(guild.id)

        async with async_session_maker() as session:
            result = await session.execute(
                select(Server).where(Server.discord_guild_id == guild_id)
            )
            server = result.scalar_one_or_none()
            if server is None:
                server = Server(discord_guild_id=guild_id, name=guild.name)
                session.add(server)
                await session.flush()

            cfg_res = await session.execute(
                select(ServerConfiguration).where(ServerConfiguration.server_id == server.id)
            )
            cfg = cfg_res.scalar_one_or_none()
            if cfg is None:
                cfg = ServerConfiguration(
                    server_id=server.id,
                    mod_review_channel_id=None,
                    moderator_role_id=None,
                    similarity_threshold=0.75,
                    vote_duration_minutes=1440,
                    majority_required=0.75,
                )
                session.add(cfg)
                await session.flush()

            await session.commit()

            cfg_snapshot = types.SimpleNamespace(
                id=cfg.id,
                server_id=cfg.server_id,
                mod_review_channel_id=cfg.mod_review_channel_id,
                moderator_role_id=cfg.moderator_role_id,
                similarity_threshold=cfg.similarity_threshold,
                vote_duration_minutes=cfg.vote_duration_minutes,
                majority_required=cfg.majority_required,
            )

        view = SetupView(guild=guild, config_snapshot=cfg_snapshot)
        embed = await view.get_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot, async_session_maker))
