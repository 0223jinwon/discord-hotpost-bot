import os
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

token = os.getenv("DISCORD_TOKEN", "").strip()
guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()

if not token:
    raise RuntimeError("DISCORD_TOKEN is not set in .env")

if not guild_id:
    raise RuntimeError("DISCORD_GUILD_ID is not set in .env")

test_guild = discord.Object(id=guild_id)

class HotPostBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        commands = await self.tree.sync(guild=test_guild)
        
    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")

bot = HotPostBot()

@bot.tree.command(
    name="ping",
    description="Check if the bot is responsive",
    guild = test_guild
)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")
    
if __name__ == "__main__":
    bot.run(token)