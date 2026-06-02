# requirements: pip install -U discord.py
import discord
from discord import app_commands
from discord.ext import commands

TOKEN = "MTUxMTQwNjkxODczNDcxMjg4Mw.G6rbj7.pkgfb4wTfJ44BMy_ISxKQJX1jcSS5tvMrojmi8"
GUILD_ID = 1194779812158525552  # Guild ID where command registers
# Add as many staff role IDs as you want here:
STAFF_ROLE_IDS = [1462534977035174191, 1482419022946369568, 1293678474900537384, 1411128656658436116]  # example: [role_id1, role_id2, ...]

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
ticket_counter = 1

# Build the embed and buttons for the menu
def menu_embed():
    e = discord.Embed(title="UGT Ticket Bot", color=discord.Color.blurple())
    e.description = (
        "Open the ticket type that fits your issue best. Please read the Terms of Service before opening any ticket.\n"
        " before opening any ticket you need to know what you want. \n"
        "**Report A Player**\nReport a player for breaking the rules.\n"
        "**General Support**\nGet help with general questions or issues.\n"
    )
    return e

class TicketMenuView(discord.ui.View):
    def __init__(self, category_id: int | None):
        super().__init__(timeout=None)
        self.category_id = category_id

    @discord.ui.button(label="Report A Player", style=discord.ButtonStyle.primary, custom_id="ticket_support")
    async def support(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_ticket_channel(interaction, "Report A Player", self.category_id)

    @discord.ui.button(label="General Support", style=discord.ButtonStyle.danger, custom_id="ticket_appeal")
    async def appeal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_ticket_channel(interaction, "General Support", self.category_id)

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        # permission check: admin or any staff role or ticket owner (owner id stored in channel.topic)
        member = interaction.user
        if member.guild_permissions.administrator or any(r.id in STAFF_ROLE_IDS for r in member.roles):
            await interaction.response.send_message("Closing ticket...", ephemeral=True)
            await interaction.channel.delete(reason="Ticket closed")
            return

        topic = interaction.channel.topic or ""
        if topic.startswith("ticket_owner:"):
            owner_id = int(topic.split(":")[1].split("|")[0])
            if owner_id == interaction.user.id:
                await interaction.response.send_message("Closing ticket...", ephemeral=True)
                await interaction.channel.delete(reason="Ticket closed by owner")
                return

        await interaction.response.send_message("Only staff or the ticket owner can close this ticket.", ephemeral=True)

async def create_ticket_channel(interaction: discord.Interaction, type_label: str, category_id: int | None):
    global ticket_counter
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    # build channel name
    safe_name = ''.join(ch for ch in interaction.user.name.lower() if ch.isalnum())[:8]
    channel_name = f"ticket-{ticket_counter}-{safe_name}"
    ticket_counter += 1

    # overwrites: deny everyone, allow member and staff roles
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False)
    }
    overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    for rid in STAFF_ROLE_IDS:
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    category = guild.get_channel(category_id) if category_id else None
    try:
        new_ch = await guild.create_text_channel(
            name=channel_name,
            topic=f"ticket_owner:{interaction.user.id} | type:{type_label}",
            overwrites=overwrites,
            category=category,
            reason=f"Ticket opened ({type_label}) by {interaction.user}"
        )

        embed = discord.Embed(
            title=f"{type_label} Ticket",
            description="please describe the issue and provide any relevant evidence or details.",
            color=discord.Color.green()
        )
        await new_ch.send(content=interaction.user.mention, embed=embed, view=CloseTicketView())
        await interaction.followup.send(f"Ticket created: {new_ch.mention}", ephemeral=True)
    except Exception:
        await interaction.followup.send("Failed to create ticket channel. Check bot permissions.", ephemeral=True)

# Slash command to post the ticket menu (admin-only)
@tree.command(name="create_ticket", description="Post the ticket menu in a channel", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(channel="Channel to post the ticket menu in", category="Optional category to place ticket channels under")
@app_commands.checks.has_permissions(administrator=True)
async def create_ticket(interaction: discord.Interaction, channel: discord.TextChannel, category: discord.CategoryChannel | None = None):
    view = TicketMenuView(category.id if category else None)
    try:
        await channel.send(embed=menu_embed(), view=view)
        await interaction.response.send_message(f"Ticket menu posted in {channel.mention}.", ephemeral=True)
    except Exception:
        await interaction.response.send_message("Failed to post menu. Check bot permissions.", ephemeral=True)

# Error handler for permission fails
@create_ticket.error
async def create_ticket_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("You need Administrator permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message("An error occurred.", ephemeral=True)

@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)