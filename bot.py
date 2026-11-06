import os
import random
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from hot_posts import DcinsideSource, FmkoreaSource, PostFetchError

load_dotenv(Path(__file__).with_name(".env"))

token = os.getenv("DISCORD_TOKEN", "").strip()
guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()

if not token:
    raise RuntimeError("DISCORD_TOKEN is not set in .env")

if not (guild_id.isascii() and guild_id.isdecimal() and 0 < int(guild_id) < 2**64):
    raise RuntimeError("DISCORD_GUILD_ID must be a valid server ID in .env")

test_guild = discord.Object(id=int(guild_id))

# Bot class which manages Discord connection and commands
class HotPostBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.post_sources = {
            "fmkorea": FmkoreaSource(),
            "dcinside": DcinsideSource(),
        }

    async def setup_hook(self):
        commands = await self.tree.sync(guild=test_guild)
        print(f"Registered {len(commands)} commands.")
        
    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")

    async def close(self):
        try:
            for source in self.post_sources.values():
                await source.close()
        finally:
            await super().close()

bot = HotPostBot()

# define '/ping' command
@bot.tree.command(
    name="ping",
    description="Check if the bot is responsive",
    guild = test_guild
)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")

# define '/top-post' command
@bot.tree.command(
    name="top-post",
    description="Send one of the top posts from internet communities",
    guild = test_guild
)
@app_commands.describe(site="Select the site to fetch posts from. Default is random.")
@app_commands.choices(site=[
    app_commands.Choice(name="Random", value="random"),
    app_commands.Choice(name="Fmkorea", value="fmkorea"),
    app_commands.Choice(name="Dcinside", value="dcinside"),
])
async def top_post(interaction: discord.Interaction, site: str = "random"):
    # Before waiting for the site response, notify Discord that processing has started.
    await interaction.response.defer(thinking=True)
    if site == "random":
        source_keys = list(bot.post_sources)
        # Select a site at random with equal probability. If that fails, try the remaining sites.
        random.shuffle(source_keys)
    else:
        source_keys = [site]

    errors = []
    for source_key in source_keys:
        try:
            posts = await bot.post_sources[source_key].get_posts()
        except PostFetchError as error:
            errors.append(str(error))
            continue

        selected = random.choice(posts)
        title = discord.utils.escape_markdown(selected["title"])
        await interaction.followup.send(
            f"**{title}**\n<{selected['url']}>",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    await interaction.followup.send("\n".join(errors), allowed_mentions=discord.AllowedMentions.none())


if __name__ == "__main__":
    bot.run(token)
