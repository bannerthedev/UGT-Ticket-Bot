# requirements: pip install -U discord.py
import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import asyncio
from dotenv import load_dotenv
load_dotenv()

GUILD_ID = 1194779812158525552
STAFF_ROLE_IDS = [1462534977035174191, 1482419022946369568, 1293678474900537384, 1411128656658436116]

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.dm_messages = True
intents.message_content = True  # required to read message content

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
ticket_counter = 1

# map channel_id -> ticket metadata
open_tickets = {}  # {channel_id: {"user_id": int, "last_user_message": datetime, "inactivity_task": task}}

# --- Embeds builders ---
def menu_embed():
    e = discord.Embed(title="UGT Tickets", color=discord.Color.blurple())
    e.add_field(name="Ultimate Gorilla Tag", value="\u200b", inline=False)
    e.add_field(
        name="\u200b",
        value=(
            "A ticket gives you direct access to the staff team for reports and support.\n"
            "Anything related to the game or the server, we can help with. Open one and\n"
            "a staff member will be with you shortly."
        ),
        inline=False
    )
    e.set_footer(text="Please read the Terms of Service before opening a ticket.")
    return e

def dm_start_embed():
    e = discord.Embed(title="UGT Ticket", description="Thanks for choosing UGT. Your ticket has now started! All messages sent here will be forwarded to the staff team.", color=discord.Color.blue())
    return e

def dm_report_question_embed():
    e = discord.Embed(description="Are you reporting a player or not?", color=discord.Color.red())
    return e

def inactivity_warning_embed():
    e = discord.Embed(title="Inactivity Warning", description="Please respond within 24 hours, or the ticket will be closed due to inactivity.", color=discord.Color.blue())
    return e

def ticket_closed_embed(reason: str):
    e = discord.Embed(title="Ticket Closed", color=discord.Color.red())
    e.add_field(name="Reason", value=reason)
    return e

# --- Views ---
class TicketMenuView(discord.ui.View):
    def __init__(self, category_id: int | None):
        super().__init__(timeout=None)
        self.category_id = category_id

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await handle_open_ticket(interaction, self.category_id)

# --- Utility: create ticket channel + DM user; attach metadata + schedule inactivity monitor ---
async def handle_open_ticket(interaction: discord.Interaction, category_id: int | None):
    global ticket_counter
    guild = interaction.guild
    user = interaction.user
    # create server channel
    safe_name = ''.join(ch for ch in user.name.lower() if ch.isalnum())[:8]
    channel_name = f"ticket-{ticket_counter}-{safe_name}"
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

    # send initial message in server ticket
    server_intro = discord.Embed(title="UGT Ticket", description=f"{user.mention} opened a ticket. Staff: respond here to talk to the user.", color=discord.Color.green())
    await new_ch.send(embed=server_intro, view=CloseTicketView())

    # DM user: start embed + question embed
    try:
        dm = await user.create_dm()
        await dm.send(embed=dm_start_embed())
        await dm.send(embed=dm_report_question_embed())
    except Exception:
        # cannot DM user
        await new_ch.send("Couldn't send DM to the user. They may have DMs disabled.")
        await interaction.followup.send("Ticket created, but couldn't DM the user.", ephemeral=True)
        # still register as open ticket
        open_tickets[new_ch.id] = {"user_id": user.id, "last_user_message": None, "inactivity_task": None}
        await interaction.followup.send(f"Ticket created: {new_ch.mention}", ephemeral=True)
        return

    # register ticket and start inactivity monitor
    open_tickets[new_ch.id] = {"user_id": user.id, "last_user_message": None, "inactivity_task": asyncio.create_task(inactivity_monitor(new_ch.id))}
    await interaction.followup.send(f"Ticket created: {new_ch.mention}", ephemeral=True)

# --- Forwarding logic: DM -> server channel ; server staff -> DM ---
@bot.event
async def on_message(message: discord.Message):
    # ignore bot messages
    if message.author.bot:
        return

    # DM from user to bot
    if isinstance(message.channel, discord.DMChannel):
        # find ticket channel for this user
        user_id = message.author.id
        # find any open ticket mapping to user_id
        ch_id = None
        for cid, meta in open_tickets.items():
            if meta["user_id"] == user_id:
                ch_id = cid
                break

        if ch_id is None:
            # no open ticket — optionally ignore or notify user
            await message.channel.send("You don't have an open ticket. Use the server ticket menu to open one.")
            return

        meta = open_tickets.get(ch_id)
        meta["last_user_message"] = discord.utils.utcnow()
        # forward message content & attachments to server channel
        guild = bot.get_guild(GUILD_ID)
        ticket_ch = guild.get_channel(ch_id)
        if not ticket_ch:
            await message.channel.send("Server ticket channel not found. It may have been closed.")
            return

        header = f"<@{user_id}> | {message.author.name} • {user_id}"
        # build content: header + content
        content = message.content or ""
        # attach attachments if present
        files = []
        for att in message.attachments:
            files.append(await att.to_file())

        # send embed-like presentation: header in bold
        send_text = f"{header}\n{content}"
        await ticket_ch.send(send_text, files=files)
        return

    # Message in a guild channel: check if it's a ticket channel
    if message.guild and message.channel.id in open_tickets:
        # staff -> forward to user; ignore user's own messages in server (they shouldn't be posting there)
        author = message.author
        meta = open_tickets[message.channel.id]
        user_id = meta["user_id"]
        # check if author is staff (role or admin)
        is_staff = author.guild_permissions.administrator or any(r.id in STAFF_ROLE_IDS for r in author.roles)
        if is_staff:
            # forward to user's DM, prefix "Staff Member" and append "videos are ok"
            try:
                user = await bot.fetch_user(user_id)
                dm = await user.create_dm()
                header = f"Staff Member • {author.display_name}"
                body = message.content or ""
                files = []
                for att in message.attachments:
                    files.append(await att.to_file())
                forward = f"**{header}**\n{body}\n\nvideos are ok"
                await dm.send(forward, files=files)
            except Exception:
                await message.channel.send("Couldn't DM the user. They may have DMs closed.")
        else:
            # optionally ignore or warn non-staff
            pass

    # allow commands to process
    await bot.process_commands(message)

# --- Inactivity monitor: after 24h of no user message, send warning; after another 24h close ticket ---
async def inactivity_monitor(channel_id: int):
    # Wait loop: check timestamps
    def get_meta():
        return open_tickets.get(channel_id)

    try:
        # wait 24h
        await asyncio.sleep(24 * 3600)
        meta = get_meta()
        if not meta:
            return  # ticket closed
        last = meta.get("last_user_message")
        # if user hasn't sent anything (None) or last message older than 24h, send warning
        if last is None or (discord.utils.utcnow() - last).total_seconds() >= 24 * 3600:
            # send warning embed to user and channel
            guild = bot.get_guild(GUILD_ID)
            ch = guild.get_channel(channel_id) if guild else None
            user = await bot.fetch_user(meta["user_id"])
            try:
                await user.send(embed=inactivity_warning_embed())
            except Exception:
                pass
            if ch:
                await ch.send(embed=inactivity_warning_embed())
            # wait another 24h
            await asyncio.sleep(24 * 3600)
            # re-check
            meta2 = get_meta()
            if not meta2:
                return
            last2 = meta2.get("last_user_message")
            if last2 is None or (discord.utils.utcnow() - last2).total_seconds() >= 24 * 3600:
                # close ticket for inactivity
                ch2 = bot.get_guild(GUILD_ID).get_channel(channel_id)
                await close_ticket_channel(ch2, reason="Inactive")
    except asyncio.CancelledError:
        return
    except Exception:
        return

# --- Close ticket helper ---
async def close_ticket_channel(channel: discord.TextChannel | None, reason: str = "Closed"):
    if not channel:
        return
    meta = open_tickets.pop(channel.id, None)
    # notify user
    if meta:
        try:
            user = await bot.fetch_user(meta["user_id"])
            await user.send(embed=ticket_closed_embed(reason))
        except Exception:
            pass
    # inform channel then delete
    try:
        await channel.send(embed=ticket_closed_embed(reason))
    except Exception:
        pass
    # cancel inactivity task if exists
    if meta and meta.get("inactivity_task"):
        task = meta["inactivity_task"]
        if not task.done():
            task.cancel()
    try:
        await asyncio.sleep(1)
        await channel.delete(reason=f"Ticket closed: {reason}")
    except Exception:
        pass

# --- Slash commands ---
@tree.command(name="create_ticket", description="Post the ticket menu in a channel", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(channel="Channel to post the ticket menu in", category="Optional category to place ticket channels under")
@app_commands.checks.has_permissions(administrator=True)
async def create_ticket(interaction: discord.Interaction, channel: discord.TextChannel, category: discord.CategoryChannel | None = None):
    view = TicketMenuView(category.id if category else None)
    embed = menu_embed()
    pfp_path = "pfp.png"
    try:
        if os.path.isfile(pfp_path):
            sent = await channel.send(file=discord.File(pfp_path))
            if sent.attachments:
                embed.set_thumbnail(url=sent.attachments[-1].url)
                await sent.delete()
                await channel.send(embed=embed, view=view)
            else:
                await channel.send(embed=embed, view=view)
        else:
            await channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"Ticket menu posted in {channel.mention}.", ephemeral=True)
    except Exception:
        await interaction.response.send_message("Failed to post menu. Check bot permissions and that pfp.png exists.", ephemeral=True)

@create_ticket.error
async def create_ticket_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("You need Administrator permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message("An error occurred.", ephemeral=True)

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

@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

bot.run(os.getenv("TOKEN"))
