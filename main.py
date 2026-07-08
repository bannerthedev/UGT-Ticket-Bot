# requirements: pip install -U discord.py
import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv()

GUILD_ID = 1194779812158525552
# Replace these with your actual staff role IDs
STAFF_ROLE_IDS = [1462534977035174191, 1482419022946369568, 1293678474900537384, 1411128656658436116]

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.dm_messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

ticket_counter = 1
open_tickets: dict[int, dict] = {}  # channel_id -> {"user_id": int, "last_user_message": datetime or None, "inactivity_task": Task}

# ---------------- Embeds ----------------
def menu_embed() -> discord.Embed:
    # Structure to resemble the example: big title, small subtitle, paragraph description
    e = discord.Embed(title="UGT Tickets", color=0x2F3430)  # dark border color to match screenshot
    # Put subtitle as small line in the description to control spacing
    e.description = (
        "Ultimate Gorilla Tag\n\n"
        "A ticket gives you direct access to the staff team for reports and support.\n"
        "Anything related to the game or the server, we can help with. Open one and\n"
        "a staff member will be with you shortly.\n\n"
        "Please read the Terms of Service before opening a ticket."
    )
    return e

def dm_start_embed() -> discord.Embed:
    return discord.Embed(
        title="UGT Ticket",
        description="Thanks for choosing COMP. Your ticket has now started! All messages sent here will be forwarded to the staff team.",
        color=discord.Color.blue()
    )

def dm_report_question_embed() -> discord.Embed:
    return discord.Embed(description="Are you reporting a player or not?", color=discord.Color.red())

def inactivity_warning_embed() -> discord.Embed:
    return discord.Embed(
        title="Inactivity Warning",
        description="Please respond within 24 hours, or the ticket will be closed due to inactivity.",
        color=discord.Color.blue()
    )

def ticket_closed_embed(reason: str) -> discord.Embed:
    e = discord.Embed(title="Ticket Closed", color=discord.Color.red())
    e.add_field(name="Reason", value=reason, inline=False)
    return e

# ---------------- View ----------------
class TicketMenuView(discord.ui.View):
    def __init__(self, category_id: int | None):
        super().__init__(timeout=None)
        self.category_id = category_id

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await handle_open_ticket(interaction, self.category_id)

# ---------------- Ticket creation ----------------
async def handle_open_ticket(interaction: discord.Interaction, category_id: int | None):
    global ticket_counter
    guild = interaction.guild
    user = interaction.user

    safe = ''.join(ch for ch in user.name.lower() if ch.isalnum())[:8]
    channel_name = f"ticket-{ticket_counter}-{safe}"
    ticket_counter += 1

    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    overwrites[user] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    for rid in STAFF_ROLE_IDS:
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    category = guild.get_channel(category_id) if category_id else None
    try:
        new_ch = await guild.create_text_channel(
            name=channel_name,
            topic=f"ticket_owner:{user.id} | type:DM Ticket",
            overwrites=overwrites,
            category=category,
            reason=f"Ticket opened by {user}"
        )
    except Exception:
        await interaction.followup.send("Failed to create ticket channel. Check bot permissions.", ephemeral=True)
        return

    server_intro = discord.Embed(
        title="UGT Ticket",
        description=f"{user.mention} opened a ticket. Staff: respond here to talk to the user.",
        color=discord.Color.green()
    )

    # Thumbnail: prefer PFP_URL env var; otherwise upload local pfp.png to obtain URL
    pfp_url = os.getenv("PFP_URL")
    try:
        if pfp_url:
            server_intro.set_thumbnail(url=pfp_url)
            await new_ch.send(embed=server_intro)
        else:
            pfp_path = "pfp.png"
            if os.path.isfile(pfp_path):
                sent = await new_ch.send(file=discord.File(pfp_path))
                if sent.attachments:
                    server_intro.set_thumbnail(url=sent.attachments[-1].url)
                    await sent.delete()
            await new_ch.send(embed=server_intro)
    except Exception:
        await new_ch.send(embed=server_intro)

    # DM the user
    try:
        dm = await user.create_dm()
        await dm.send(embed=dm_start_embed())
        await dm.send(embed=dm_report_question_embed())
    except Exception:
        await new_ch.send("Couldn't send DM to the user. They may have DMs disabled.")
        await interaction.followup.send("Ticket created, but couldn't DM the user.", ephemeral=True)
        open_tickets[new_ch.id] = {"user_id": user.id, "last_user_message": None, "inactivity_task": None}
        return

    task = asyncio.create_task(inactivity_monitor(new_ch.id))
    open_tickets[new_ch.id] = {"user_id": user.id, "last_user_message": None, "inactivity_task": task}
    await interaction.followup.send(f"Ticket created: {new_ch.mention}", ephemeral=True)

# ---------------- Forwarding (DM <-> Server) ----------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # DM from user -> forward into server ticket as embed
    if isinstance(message.channel, discord.DMChannel):
        user_id = message.author.id
        ch_id = None
        for cid, meta in open_tickets.items():
            if meta["user_id"] == user_id:
                ch_id = cid
                break

        if ch_id is None:
            await message.channel.send("You don't have an open ticket. Use the server ticket menu to open one.")
            return

        meta = open_tickets.get(ch_id)
        meta["last_user_message"] = discord.utils.utcnow()

        guild = bot.get_guild(GUILD_ID)
        ticket_ch = guild.get_channel(ch_id) if guild else None
        if not ticket_ch:
            await message.channel.send("Server ticket channel not found. It may have been closed.")
            return

        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=f"{message.author} • {user_id}", icon_url=(message.author.avatar.url if message.author.avatar else None))
        embed.description = message.content or "\u200b"
        files = [await att.to_file() for att in message.attachments]
        await ticket_ch.send(content=f"<@{user_id}>", embed=embed, files=files)
        return

    # Server ticket -> staff messages forwarded to user's DM as embed
    if message.guild and message.channel.id in open_tickets:
        author = message.author
        meta = open_tickets[message.channel.id]
        user_id = meta["user_id"]
        is_staff = author.guild_permissions.administrator or any(r.id in STAFF_ROLE_IDS for r in author.roles)
        if is_staff:
            try:
                user = await bot.fetch_user(user_id)
                dm = await user.create_dm()
                embed = discord.Embed(color=discord.Color.green())
                embed.set_author(name=f"Staff Member • {author.display_name}", icon_url=(author.avatar.url if author.avatar else None))
                embed.description = message.content or "\u200b"
                embed.set_footer(text="videos are ok")
                files = [await att.to_file() for att in message.attachments]
                await dm.send(embed=embed, files=files)
            except Exception:
                await message.channel.send("Couldn't DM the user. They may have DMs closed.")
    await bot.process_commands(message)

# ---------------- Inactivity monitor ----------------
async def inactivity_monitor(channel_id: int):
    def get_meta():
        return open_tickets.get(channel_id)
    try:
        await asyncio.sleep(24 * 3600)
        meta = get_meta()
        if not meta:
            return
        last = meta.get("last_user_message")
        if last is None or (discord.utils.utcnow() - last).total_seconds() >= 24 * 3600:
            try:
                user = await bot.fetch_user(meta["user_id"])
                await user.send(embed=inactivity_warning_embed())
            except Exception:
                pass
            guild = bot.get_guild(GUILD_ID)
            ch = guild.get_channel(channel_id) if guild else None
            if ch:
                await ch.send(embed=inactivity_warning_embed())
            await asyncio.sleep(24 * 3600)
            meta2 = get_meta()
            if not meta2:
                return
            last2 = meta2.get("last_user_message")
            if last2 is None or (discord.utils.utcnow() - last2).total_seconds() >= 24 * 3600:
                ch2 = bot.get_guild(GUILD_ID).get_channel(channel_id)
                await close_ticket_channel(ch2, reason="Inactive")
    except asyncio.CancelledError:
        return
    except Exception:
        return

# ---------------- Close helper ----------------
async def close_ticket_channel(channel: discord.TextChannel | None, reason: str = "Closed"):
    if not channel:
        return
    meta = open_tickets.pop(channel.id, None)
    if meta:
        try:
            user = await bot.fetch_user(meta["user_id"])
            await user.send(embed=ticket_closed_embed(reason))
        except Exception:
            pass
    try:
        await channel.send(embed=ticket_closed_embed(reason))
    except Exception:
        pass
    if meta and meta.get("inactivity_task"):
        task = meta["inactivity_task"]
        if not task.done():
            task.cancel()
    try:
        await asyncio.sleep(1)
        await channel.delete(reason=f"Ticket closed: {reason}")
    except Exception:
        pass

# ---------------- create_ticket command (logo + button under image) ----------------
@tree.command(name="create_ticket", description="Post the ticket menu in a channel", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(channel="Channel to post the ticket menu in", category="Optional category to place ticket channels under")
@app_commands.checks.has_permissions(administrator=True)
async def create_ticket(interaction: discord.Interaction, channel: discord.TextChannel, category: discord.CategoryChannel | None = None):
    await interaction.response.defer(ephemeral=True)
    view = TicketMenuView(category.id if category else None)
    embed = menu_embed()
    pfp_path = "pfp.png"
    try:
        # If local image exists, send it with the view so button sits under it, then edit same message to include embed thumbnail
        if os.path.isfile(pfp_path):
            sent = await channel.send(file=discord.File(pfp_path), view=view)
            if sent.attachments:
                embed.set_thumbnail(url=sent.attachments[-1].url)
            await sent.edit(embed=embed)
        else:
            pfp_url = os.getenv("PFP_URL")
            if pfp_url:
                embed.set_thumbnail(url=pfp_url)
            await channel.send(embed=embed, view=view)
        await interaction.followup.send(f"Ticket menu posted in {channel.mention}.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("Failed to post menu. Check bot permissions and that pfp.png or PFP_URL exists.", ephemeral=True)
        print("create_ticket error:", e)

@create_ticket.error
async def create_ticket_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("You need Administrator permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message("An error occurred.", ephemeral=True)

# ---------------- close-ticket command ----------------
@tree.command(name="close-ticket", description="Close the ticket you are in (staff only)", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(reason="Reason for closing the ticket (optional)")
@app_commands.checks.has_permissions(administrator=True)
async def close_ticket(interaction: discord.Interaction, reason: str = "Inactive"):
    ch = interaction.channel
    if not ch or not isinstance(ch, discord.TextChannel) or ch.id not in open_tickets:
        await interaction.response.send_message("This command can only be used in an open ticket channel.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await close_ticket_channel(ch, reason=reason)
    await interaction.followup.send("Ticket closed.", ephemeral=True)

@close_ticket.error
async def close_ticket_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("You need Administrator permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message("An error occurred while trying to close the ticket.", ephemeral=True)

# ---------------- Ready ----------------
@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

bot.run(os.getenv("TOKEN"))
