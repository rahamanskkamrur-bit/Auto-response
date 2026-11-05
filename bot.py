import asyncio, json, os, aiohttp
from aiohttp import web
import discord
from discord.ext import commands

# =====================================================================
# 1. CONFIGURATION
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
STAFF_ROLE_IDS = [1567026563369664590]
TICKET_CATEGORY_ID = 1567026565483859991
BOT_STATUS = "Starting..."

# Tracks tickets where a human staff member has taken over (or paused AI)
PAUSED_TICKETS = set()

# =====================================================================
# 2. 24/7 RENDER KEEP-ALIVE SERVER & AUTO-PINGER
# =====================================================================
async def handle_ping(req):
    return web.Response(text=f"AI Support Bot 24/7 | Status: {BOT_STATUS}")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()

async def self_ping_loop():
    await asyncio.sleep(30)
    url = os.environ.get("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/"
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async with s.get(url, timeout=10): pass
            except Exception: pass
            await asyncio.sleep(300)

# =====================================================================
# 3. SMART SUPPORT AI ENGINE (Answers while Staff are Busy)
# =====================================================================
def smart_fallback_reply(msg_text: str, channel_name: str) -> str:
    t = msg_text.lower()
    if any(w in t for w in ["hello", "hi", "hey", "anyone there", "help"]):
        return (
            "👋 Hello! Our human support staff are currently busy assisting other members, so I am stepping in to help!\n\n"
            "Please **describe your issue in detail** (include any screenshots, usernames, or IDs) so everything is ready the moment a staff member arrives."
        )
    if "report" in channel_name or any(w in t for w in ["report", "scam", "rule", "toxic", "hacker", "abuse"]):
        return (
            "🚨 **Member Report Assistant:** While our staff team is busy, please make sure you have posted:\n"
            "1️⃣ The **Username & Discord ID** of the person you are reporting.\n"
            "2️⃣ **Uncropped screenshots or video evidence** of what happened.\n"
            "A moderator will review your evidence as soon as they are free!"
        )
    if "partner" in channel_name or any(w in t for w in ["partner", "ally", "affiliate", "collab"]):
        return (
            "🤝 **Partnership Assistant:** Our Partnership Managers are currently occupied. Please share:\n"
            "• Your **Server Name & Invite Link**\n"
            "• Your **Member Count**\n"
            "• What type of partnership you are looking for!"
        )
    if "appeal" in channel_name or any(w in t for w in ["ban", "mute", "unban", "unmute", "appeal"]):
        return (
            "⚖️ **Appeal Assistant:** While moderators are busy, please state:\n"
            "• Your **Username / ID**\n"
            "• The **reason** you were muted or banned\n"
            "• Why you believe the punishment should be lifted."
        )
    if any(w in t for w in ["role", "verify", "permission", "channel", "locked"]):
        return (
            "🔑 **Role & Access Help:** If you are missing roles or channel access, make sure you have clicked the **Agree** button in the `#rules` channel! "
            "If you still need a specific role, please state which role you need and a staff member will grant it shortly."
        )
    return (
        "Thank you for providing those details! 📝 I have logged your message for our support team.\n"
        "All staff members are currently busy, but **your ticket is in the queue**. Feel free to drop any extra screenshots or info here while you wait!"
    )

async def generate_support_reply(user_name: str, msg_text: str, channel_name: str) -> str:
    if not GEMINI_API_KEY:
        return smart_fallback_reply(msg_text, channel_name)

    sys_inst = (
        "You are a polite, helpful Discord Support Ticket Assistant stepping in because human staff are currently busy. "
        "Keep answers concise (2-4 sentences), friendly, and helpful. Ask the user for relevant details (screenshots, IDs, explanation) "
        "so human staff have everything ready when they arrive."
    )
    prompt = f"Ticket Channel: #{channel_name}\nUser ({user_name}) says: {msg_text}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": sys_inst}]},
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, timeout=12) as r:
                if r.status == 200:
                    data = await r.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        pass
    return smart_fallback_reply(msg_text, channel_name)

# =====================================================================
# 4. TICKET CONTROLS & STAFF TAKEOVER BUTTONS
# =====================================================================
def is_staff(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    return member.guild_permissions.administrator or member.guild_permissions.manage_channels or any(r.id in STAFF_ROLE_IDS for r in member.roles)

def is_ticket_channel(channel: discord.TextChannel) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    if channel.category_id == TICKET_CATEGORY_ID:
        return True
    if channel.topic and "Ticket" in channel.topic:
        return True
    return channel.name.startswith(("support-", "report-", "appeal-", "partner-", "ticket-"))

class AIControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Staff: Claim & Pause AI", style=discord.ButtonStyle.success, emoji="🙋‍♂️", custom_id="ai_sup:pause")
    async def pause_ai_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Only support staff can claim tickets and pause the AI.", ephemeral=True)
        PAUSED_TICKETS.add(interaction.channel.id)
        await interaction.response.send_message(
            f"✅ **{interaction.user.mention} has taken over this ticket!**\n*(🤖 AI Auto-Responder is now paused so staff can assist you directly.)*"
        )

    @discord.ui.button(label="Staff: Resume AI", style=discord.ButtonStyle.secondary, emoji="🤖", custom_id="ai_sup:resume")
    async def resume_ai_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Only support staff can resume the AI.", ephemeral=True)
        PAUSED_TICKETS.discard(interaction.channel.id)
        await interaction.response.send_message("🤖 **AI Support Assistant resumed!** I will continue helping while staff are away.", ephemeral=True)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ai_sup:close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"🔒 Ticket closed by {interaction.user.mention}. Deleting in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

# =====================================================================
# 5. BOT SETUP & AUTOMATIC BUSY-STAFF DETECTOR
# =====================================================================
class SupportAIBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(AIControlView())
        await self.tree.sync()

    async def on_ready(self):
        global BOT_STATUS
        BOT_STATUS = f"ONLINE as {self.user}"
        print(f"✅ AI Support Bot ONLINE as {self.user}")

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await self.process_commands(message)

        if not is_ticket_channel(message.channel):
            return

        # If a Staff Member sends a message in the ticket, automatically pause the AI so staff can talk!
        if is_staff(message.author):
            if message.channel.id not in PAUSED_TICKETS:
                PAUSED_TICKETS.add(message.channel.id)
            return

        # If a Staff Member already claimed the ticket or replied, stay quiet
        if message.channel.id in PAUSED_TICKETS:
            return

        # Wait 6 seconds to see if a staff member replies first, then respond with AI
        async with message.channel.typing():
            await asyncio.sleep(4)
            if message.channel.id in PAUSED_TICKETS:
                return
            reply_text = await generate_support_reply(message.author.display_name, message.content, message.channel.name)

        embed = discord.Embed(
            title="🤖 Automated Support Assistant (Staff Busy)",
            description=reply_text,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="A human staff member will take over as soon as they are available.")
        await message.channel.send(content=message.author.mention, embed=embed, view=AIControlView())

def reg_cmds(b: SupportAIBot):
    @b.tree.command(name="ai_assist", description="Manually trigger the AI Support Assistant in this ticket")
    async def ai_assist_slash(interaction: discord.Interaction):
        PAUSED_TICKETS.discard(interaction.channel.id)
        emb = discord.Embed(
            title="🤖 AI Support Assistant Active",
            description="Hello! Our support staff are currently busy, so I am here to assist you. Please tell me what you need help with!",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=emb, view=AIControlView())

async def main():
    await run_web_server()
    asyncio.create_task(self_ping_loop())
    if not BOT_TOKEN:
        while True: await asyncio.sleep(3600)
    bot = SupportAIBot()
    reg_cmds(bot)
    await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
