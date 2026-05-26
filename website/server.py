from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import sys
import uuid
import logging
import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv

# Base Setup
BASE_DIR = Path(__file__).parent
sys.path.append(str(BASE_DIR.parent))

from database import db_manager
import brain

load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prime_os")

# Configuration Audit & Sanitization
def get_env_safe(key, default=""):
    val = os.getenv(key, default).strip().replace('"', '').replace("'", "")
    if val:
        # Log masked version for safety and verification
        logger.info(f"✅ CONFIG: Loaded {key} ({val[:4]}...{val[-4:]})")
    else:
        logger.error(f"❌ CONFIG: Variable {key} is MISSING or EMPTY!")
    return val

CLIENT_ID = get_env_safe("DISCORD_CLIENT_ID")
CLIENT_SECRET = get_env_safe("DISCORD_CLIENT_SECRET")
BOT_TOKEN = get_env_safe("DISCORD_TOKEN")
REDIRECT_URI = get_env_safe("DISCORD_REDIRECT_URI")

# Railway Environment Detection
RAILWAY_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN") or os.getenv("RAILWAY_STATIC_URL")

# Smart Overwrite: If it's localhost but we have a Railway domain, it's definitely wrong.
if ("localhost" in REDIRECT_URI or not REDIRECT_URI) and RAILWAY_DOMAIN:
    # Ensure domain has no protocol for a clean join
    clean_domain = RAILWAY_DOMAIN.replace("https://", "").replace("http://", "").rstrip("/")
    REDIRECT_URI = f"https://{clean_domain}/callback"
    logger.info(f"🚀 CLOUD OVERRIDE: Redirect URI forced to {REDIRECT_URI}")

# Final Fallback
if not REDIRECT_URI:
    REDIRECT_URI = "http://localhost:8000/callback"
    logger.warning("⚠️ CONFIG: Defaulting to localhost REDIRECT_URI.")

logger.info(f"🎯 FINAL REDIRECT_URI: {REDIRECT_URI}")

if not CLIENT_ID or not BOT_TOKEN:
    print("\n" + "!"*60)
    print("CRITICAL CONFIG ERROR: Your Discord Client ID or Bot Token is missing.")
    print("Please check your Railway Variables tab immediately!")
    print("!"*60 + "\n")

# SERVER-SIDE STORAGE
SESSIONS = {}
BOT_GUILDS = set()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# BOT UTILS
# --------------------------------------------------------------------------

async def update_bot_guilds():
    """Fetch all guilds the bot is currently in."""
    global BOT_GUILDS
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                "https://discord.com/api/v10/users/@me/guilds",
                headers={"Authorization": f"Bot {BOT_TOKEN}"}
            )
            if res.status_code == 200:
                guilds = res.json()
                BOT_GUILDS = {str(g["id"]) for g in guilds}
                logger.info(f"Bot is in {len(BOT_GUILDS)} servers.")
    except Exception as e:
        logger.error(f"Failed to fetch bot guilds: {e}")

@app.on_event("startup")
async def startup_event():
    await update_bot_guilds()

# --------------------------------------------------------------------------
# AUTH ENGINE
# --------------------------------------------------------------------------

@app.get("/login")
async def login():
    scopes = "identify guilds"
    url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope={scopes}"
    return RedirectResponse(url=url)

@app.get("/callback")
async def callback(code: str = None):
    if not code: return RedirectResponse(url="/dashboard/index.html?error=no_code")
    
    async with httpx.AsyncClient() as client:
        token_res = await client.post("https://discord.com/api/oauth2/token", data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI
        })
        if token_res.status_code != 200: return RedirectResponse(url="/dashboard/index.html?error=auth_failed")
        
        token_data = token_res.json()
        access_token = token_data["access_token"]
        
        user_res = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
        guilds_res = await client.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"})
        
        user_info = user_res.json()
        guilds = guilds_res.json()
        
        user_id = int(user_info["id"])
        
        # Check if user already exists
        mem = db_manager.get_user_memory(user_id)
        is_new_user = False
        if not mem:
            is_new_user = True
            db_manager.update_user_memory(user_id, user_info["username"], profile_summary="Authenticated user via web dashboard", vibe="neutral")
            
        session_token = str(uuid.uuid4())
        SESSIONS[session_token] = {
            "user": {
                "id": user_info["id"], 
                "name": user_info["username"], 
                "avatar": user_info.get("avatar"),
                "is_new_user": is_new_user
            },
            "guilds": guilds
        }
        return RedirectResponse(url=f"/dashboard/index.html?session_token={session_token}")

@app.get("/api/me")
async def api_me(request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: return JSONResponse({"authenticated": False}, status_code=401)
    
    # Refresh bot guilds list on check
    await update_bot_guilds()
    
    data = SESSIONS[token]
    # Enrich guilds with 'bot_present' flag
    enriched_guilds = []
    for g in data["guilds"]:
        g_copy = g.copy()
        g_copy["bot_present"] = str(g["id"]) in BOT_GUILDS
        enriched_guilds.append(g_copy)
    
    return {"authenticated": True, "user": data["user"], "guilds": enriched_guilds}

@app.post("/api/user/preference")
async def save_user_preference(request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    data = await request.json()
    pref = data.get("preference", "email") # 'email', 'discord', 'both'
    user_id = int(SESSIONS[token]["user"]["id"])
    username = SESSIONS[token]["user"]["name"]
    
    # Save choice in database
    db_manager.set_user_notification_preference(user_id, pref)
    
    # 3. Welcome Message Dispatching
    # If user chooses Discord DM or Both, send welcome message via the bot
    if pref in ["discord", "both"]:
        asyncio.create_task(send_bot_dm_welcome(user_id, username))
    
    # If user chooses Email or Both, send via email (mocking Resend / printing log for backward compatibility)
    if pref in ["email", "both"]:
        logger.info(f"📧 Resend API Triggered: Welcoming {username} ({user_id}) via Email.")
        
    return {"status": "success"}

async def send_bot_dm_welcome(user_id: int, username: str):
    """Sends a premium direct message welcome using Discord API Bot authorization."""
    try:
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
            # 1. Create DM channel
            dm_channel_res = await client.post(
                "https://discord.com/api/v10/users/@me/channels",
                headers=headers,
                json={"recipient_id": str(user_id)}
            )
            if dm_channel_res.status_code == 200:
                channel_id = dm_channel_res.json()["id"]
                # 2. Send welcome embed
                embed = {
                    "title": "🌌 WELCOME TO LUMORA",
                    "description": (
                        f"Hey **{username}**! Welcome to the squad.\n\n"
                        "We've updated your settings successfully. You will receive real-time notifications & OTPs right here in your DMs!\n\n"
                        "💡 *Need help configuring your servers? Type `!help` or head over to the web dashboard.*"
                    ),
                    "color": 65450, # Lumora vibe color (cyanish-green)
                    "footer": {"text": "Lumora AI • Creative Engineering"}
                }
                await client.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers=headers,
                    json={"embeds": [embed]}
                )
                logger.info(f"✨ Welcome DM successfully delivered to user {user_id}")
            else:
                logger.error(f"❌ Failed to open DM channel with user {user_id}: {dm_channel_res.text}")
    except Exception as e:
        logger.error(f"❌ Failed to dispatch DM welcome to user {user_id}: {e}")

@app.get("/api/dashboard/stats")
async def dash_stats(request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        with db_manager.get_connection() as conn:
            with db_manager.get_cursor(conn) as cursor:
                cursor.execute("SELECT COUNT(*) FROM user_levels")
                user_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM conversation_history")
                msg_count = cursor.fetchone()[0]
                
                cursor.execute("SELECT user_id, xp, level FROM user_levels ORDER BY xp DESC LIMIT 5")
                lb_rows = cursor.fetchall()
                leaderboard = []
                for row in lb_rows:
                    cursor.execute("SELECT username FROM user_memory WHERE user_id = %s" if db_manager.is_postgres else "SELECT username FROM user_memory WHERE user_id = ?", (row[0],))
                    u_row = cursor.fetchone()
                    leaderboard.append({
                        "id": str(row[0]),
                        "xp": row[1],
                        "level": row[2],
                        "username": u_row[0] if u_row else f"USER_{str(row[0])[-4:]}"
                    })

                return {
                    "users": user_count, 
                    "messages": msg_count, 
                    "status": "NOMINAL", 
                    "bot_servers": len(BOT_GUILDS),
                    "leaderboard": leaderboard
                }
    except Exception as e: 
        logger.error(f"Dash Stats Error: {e}")
        return {"error": "DB Error"}

@app.get("/api/analytics/summary")
async def api_analytics(request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        with db_manager.get_connection() as conn:
            with db_manager.get_cursor(conn) as cursor:
                cursor.execute("SELECT COUNT(*) FROM conversation_history WHERE role = 'user'")
                cmd_count = cursor.fetchone()[0]
                cursor.execute("SELECT AVG(level) FROM user_levels")
                avg_lvl = cursor.fetchone()[0] or 0
                return {
                    "commands_total": cmd_count,
                    "avg_level": round(avg_lvl, 1),
                    "retention": "94.2%",
                    "latency": "1.1s"
                }
    except: return {"error": "Failed to fetch analytics"}

@app.post("/api/guilds/{guild_id}/message")
async def send_custom_message(guild_id: str, request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: raise HTTPException(status_code=401)
    data = await request.json()
    channel_id = data.get("channel_id")
    content = data.get("content")
    if not channel_id or not content: return {"error": "Missing params"}
    async with httpx.AsyncClient() as client:
        res = await client.post(f"https://discord.com/api/v10/channels/{channel_id}/messages", 
                               headers={"Authorization": f"Bot {BOT_TOKEN}"}, json={"content": content})
        return {"status": "success"} if res.status_code == 200 else {"status": "failed", "error": res.text}

@app.get("/api/guilds/{guild_id}/settings")
async def get_settings(guild_id: str, request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: raise HTTPException(status_code=401)
    settings = db_manager.get_guild_setting(guild_id, "all_settings", {"prefix": "!", "vibe": "helpful"})
    return settings

@app.get("/api/guilds/{guild_id}/roles")
async def get_roles(guild_id: str, request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: raise HTTPException(status_code=401)
    async with httpx.AsyncClient() as client:
        res = await client.get(f"https://discord.com/api/v10/guilds/{guild_id}/roles", headers={"Authorization": f"Bot {BOT_TOKEN}"})
        if res.status_code == 200: return res.json()
    return []

@app.get("/api/guilds/{guild_id}/channels")
async def get_channels(guild_id: str, request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: raise HTTPException(status_code=401)
    async with httpx.AsyncClient() as client:
        res = await client.get(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers={"Authorization": f"Bot {BOT_TOKEN}"})
        if res.status_code == 200: return res.json()
    return []

@app.post("/api/guilds/{guild_id}/ai-suggest")
async def ai_suggest_config(guild_id: str, request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: raise HTTPException(status_code=401)
    
    if not brain.GEMINI_KEYS:
        return {"status": "error", "error": "AI module unavailable."}

    # Fetch context: Channels and Roles
    async with httpx.AsyncClient() as client:
        # Get roles
        r_res = await client.get(f"https://discord.com/api/v10/guilds/{guild_id}/roles", headers={"Authorization": f"Bot {BOT_TOKEN}"})
        # Get channels
        c_res = await client.get(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers={"Authorization": f"Bot {BOT_TOKEN}"})
        
        roles = r_res.json() if r_res.status_code == 200 else []
        channels = c_res.json() if c_res.status_code == 200 else []

    # Prepare context for AI (TRIMMED for performance)
    chan_list = [{"id": c["id"], "name": c["name"]} for c in channels if c["type"] in [0, 5]][:60]
    role_list = [{"id": r["id"], "name": r["name"]} for r in roles if r["name"] != "@everyone" and not r.get("managed")][:60]

    system_instr = """You are a Discord AI Auditor.
1. Map existing channels/roles to these keys: welcome_channel, log_channel, rules_channel, roles_channel, verification_channel, leveling_channel, general_channel, verified_role, unverified_role, muted_role.
2. If a key from the list above cannot be mapped to an existing channel/role (i.e. it's MISSING), add it to a 'creation_suggestions' list with a recommended name and type.
3. If a role has no color, suggest a creative hex color.

OUTPUT JSON ONLY:
{
  "mappings": { "key": "id", ... },
  "creation_suggestions": [ {"key": "...", "recommended_name": "...", "type": "channel|role"}, ... ],
  "role_color_suggestions": [ {"id": "...", "suggested_color": "#hex"}, ... ],
  "reasoning": "Quick logic summary."
}
"""

    context_str = f"CHANNELS: {json.dumps(chan_list)}\nROLES: {json.dumps(role_list)}"
    
    try:
        from brain import safe_generate_content, PRIMARY_MODEL, types
        # Use a slightly lower temperature for deterministic & faster results
        response = await safe_generate_content(
            model=PRIMARY_MODEL, 
            contents=f"{system_instr}\n\nSERVER CONTEXT:\n{context_str}\n\nOutput JSON:",
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        )
        
        if not response or not response.text:
            return {"status": "error", "error": "AI calculation timed out."}
        
        suggestions = json.loads(response.text)
        return {"status": "success", "suggestions": suggestions}
    except Exception as e:
        logger.error(f"AI Suggest Error: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/api/guilds/{guild_id}/apply-suggestions")
async def apply_suggestions(guild_id: str, request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: raise HTTPException(status_code=401)
    
    data = await request.json()
    color_updates = data.get("color_updates", [])
    
    async with httpx.AsyncClient() as client:
        for update in color_updates:
            role_id = update.get("id")
            hex_color = update.get("suggested_color", "0").replace("#", "")
            if role_id and hex_color != "0":
                try:
                    color_int = int(hex_color, 16)
                    # PATCH role color
                    await client.patch(
                        f"https://discord.com/api/v10/guilds/{guild_id}/roles/{role_id}",
                        headers={"Authorization": f"Bot {BOT_TOKEN}"},
                        json={"color": color_int}
                    )
                except Exception as e:
                    logger.error(f"Failed to update role color {role_id}: {e}")
                    
    return {"status": "success"}

@app.post("/api/guilds/{guild_id}/ai-plan")
async def ai_plan(guild_id: str, request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: raise HTTPException(status_code=401)
    
    if not brain.GEMINI_KEYS:
        return {"status": "error", "error": "CRITICAL: Gemini API Key is missing from .env"}

    data = await request.json()
    user_prompt = data.get("prompt")
    if not user_prompt: return {"error": "No prompt provided"}

    system_instr = """You are a Discord Server Architect.
Interpret the user's request and output a JSON list of actions to structure their server.
Valid Actions:
- {"action": "create_category", "name": "..."}
- {"action": "create_channel", "name": "...", "type": "text|voice", "category": "..."}
- {"action": "create_role", "name": "...", "color": "hex_code", "icon": "emoji"}

RULES:
1. Only return the JSON list. No explanation.
2. If a category is mentioned for a channel, ensure you create_category first.
3. Be CREATIVE with icons/emojis for roles based on the user's suggestion.
4. If the user mentions a color (e.g. 'navy blue'), find the hex code.
5. Limit to max 12 actions per plan.
"""

    try:
        from brain import safe_generate_content, PRIMARY_MODEL, types
        full_prompt = f"Context: Creating a Discord server structure.\nUser Goal: {user_prompt}\n\nInstruction: {system_instr}\n\nOutput JSON Action List:"

        response = await safe_generate_content(
            model=PRIMARY_MODEL,
            contents=full_prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.7)
        )
        
        if not response or not response.text:
            return {"status": "error", "error": "AI failed to generate a plan. Try be more specific."}
        
        raw_text = response.text.strip()
        if raw_text.startswith("```json"): raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif raw_text.startswith("```"): raw_text = raw_text.split("```")[1].split("```")[0].strip()

        try:
            plan = json.loads(raw_text)
            if not isinstance(plan, list): plan = [plan]
            return {"status": "success", "plan": plan}
        except Exception as json_err:
            logger.error(f"AI Plan JSON Parse Error: {json_err} | Raw: {raw_text}")
            return {"status": "error", "error": "AI returned invalid data format."}
    except Exception as e:
        logger.error(f"AI Plan Critical Error: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/api/guilds/{guild_id}/ai-execute")
async def ai_execute(guild_id: str, request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: raise HTTPException(status_code=401)
    data = await request.json()
    plan = data.get("plan", [])
    if not plan: return {"error": "No plan provided"}

    results = []
    categories = {}
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
        for task in plan:
            action = task.get("action")
            name = task.get("name")
            if action == "create_category":
                res = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={"name": name, "type": 4})
                if res.status_code == 201: categories[name] = res.json()["id"]; results.append(f"Created category: {name}")
            elif action == "create_channel":
                c_type = 0 if task.get("type") == "text" else 2
                payload = {"name": name, "type": c_type}
                cat_name = task.get("category"); 
                if cat_name in categories: payload["parent_id"] = categories[cat_name]
                res = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json=payload)
                if res.status_code == 201: results.append(f"Created channel: {name}")
            elif action == "create_role":
                name = task.get("name")
                icon = task.get("icon", "")
                full_name = f"{icon} {name}".strip() if icon else name
                color_hex = task.get("color", "0").replace("#", "")
                color_int = int(color_hex, 16) if color_hex != "0" else 0
                res = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/roles", headers=headers, json={"name": full_name, "color": color_int})
                if res.status_code in [200, 201]: results.append(f"Created role: {full_name}")
    return {"status": "success", "results": results}

@app.post("/api/guilds/{guild_id}/settings")
async def save_settings(guild_id: str, request: Request):
    token = request.headers.get("X-Session-Token")
    if not token or token not in SESSIONS: raise HTTPException(status_code=401)
    data = await request.json()
    db_manager.save_guild_setting(guild_id, "all_settings", data)
    return {"status": "success"}

@app.post("/api/guilds/{guild_id}/trigger")
async def trigger_action(guild_id: str, request: Request, token: str = None):
    if not token or token not in SESSIONS: raise HTTPException(status_code=401)
    data = await request.json()
    action = data.get("action")
    
    settings = db_manager.get_guild_setting(guild_id, "all_settings", {})
    
    async with httpx.AsyncClient() as client:
        if action == "verification":
            chan_id = settings.get("verification_channel")
            if not chan_id: return {"error": "Verification channel not set"}
            
            payload = {
                "embeds": [{
                    "title": "🛡️ ACCOUNT VERIFICATION",
                    "description": (
                        "Welcome! To prevent automated bot accounts, we require all members to complete a quick verification check.\n\n"
                        "**How it Works:**\n"
                        "1️⃣ Click the **'Verify Myself'** button below.\n"
                        "2️⃣ A captcha image will appear.\n"
                        "3️⃣ Click **'Enter Code'** and type what you see.\n\n"
                        "*Need help? Contact a moderator.*"
                    ),
                    "color": 65280 # Green
                }],
                "components": [{
                    "type": 1,
                    "components": [{
                        "type": 2, "style": 3, "label": "Verify Myself", "custom_id": "verify_start_btn", "emoji": {"name": "🛡️"}
                    }]
                }]
            }
            res = await client.post(f"https://discord.com/api/v10/channels/{chan_id}/messages", 
                                   headers={"Authorization": f"Bot {BOT_TOKEN}"}, json=payload)
            return {"status": "success", "message": "DONE!"} if res.status_code == 200 else {"status": "failed"}

        if action == "roles":
            chan_id = settings.get("roles_channel") or settings.get("role_request_channel")
            if not chan_id: return {"error": "Roles channel not set"}
            
            async with httpx.AsyncClient() as client:
                # Fetch REAL roles from Discord to make it dynamic
                r_res = await client.get(f"https://discord.com/api/v10/guilds/{guild_id}/roles", headers={"Authorization": f"Bot {BOT_TOKEN}"})
                if r_res.status_code != 200: return {"error": "Failed to fetch guild roles"}
                
                all_roles = r_res.json()
                # Filter out @everyone, bot roles, and managed roles
                valid_roles = [r for r in all_roles if r["name"] != "@everyone" and not r.get("managed", False)]
                # Take top 5 roles for the interactive menu (Discord limit per row)
                display_roles = valid_roles[:5]

                role_mentions = "\n".join([f"✨ <@&{r['id']}> - {r['name']}" for r in display_roles])
                
                payload = {
                    "embeds": [{
                        "title": "🎭 COMMUNITY SECTORS",
                        "description": (
                            "Select your sectors below to unlock restricted access and specialized channels.\n\n"
                            f"{role_mentions}\n\n"
                            "*Click buttons to toggle access.*"
                        ),
                        "color": 11468718 # Blurple
                    }],
                    "components": [
                        {
                            "type": 1,
                            "components": [
                                {"type": 2, "style": 2, "label": r["name"][:32], "custom_id": f"role_{r['id']}"} 
                                for r in display_roles
                            ]
                        }
                    ]
                }
                res = await client.post(f"https://discord.com/api/v10/channels/{chan_id}/messages", 
                                       headers={"Authorization": f"Bot {BOT_TOKEN}"}, json=payload)
                return {"status": "success", "message": "DONE!"} if res.status_code == 200 else {"status": "failed"}

    return {"error": "Invalid action"}


# --------------------------------------------------------------------------
# STATIC
# --------------------------------------------------------------------------
@app.get("/api/invite-url")
async def get_invite(guild_id: str = None):
    base_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&permissions=8&scope=bot%20applications.commands"
    if guild_id:
        base_url += f"&guild_id={guild_id}&disable_guild_select=true"
    return {"url": base_url}

app.mount("/dashboard", StaticFiles(directory=BASE_DIR / "dashboard"), name="dashboard")
@app.get("/{path:path}")
async def catch_all(path: str):
    p = BASE_DIR / path
    if p.is_file(): return FileResponse(p)
    return FileResponse(BASE_DIR / "index.html")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
