"""
Excel Financial — Attendance & Culture Bot  (v4)
=================================================
  VERIFICATION   — "Request Access" button in #start-here -> Approve/Deny in
                   #access-requests -> grants Verified Member role.
  DAILY (private, #attendance-log)
                 — real-time on-time / LATE clock-ins;
                 — 6:00 PM PT report: late, left-before-6, full-day, no-shows,
                   camera compliance, violation points (late + early only),
                   and AUTO-FLAGS that ping you for repeat offenders.
  CAMERA         — tracks % of room time each person has their camera ON and
                   flags anyone under 50% (your camera-on culture, enforced).
  WEEKLY         — Saturdays 10:00 AM PT:
                   PUBLIC  (#weekly-report): top hours + perfect-week shout-outs;
                   PRIVATE (#attendance-log): top violators + at-risk + low-camera.

Violation POINTS count only late + early. Camera and no-shows are reported for
awareness but don't add points. Times are Pacific (auto PST/PDT). AFK not counted.
Python 3.9+, discord.py 2.3+.  Set DISCORD_TOKEN in the environment.
"""

import os
import json
import datetime as dt
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import tasks

# ---------------------------------------------------------------------------
GUILD_ID              = 1530612426133868574
VERIFIED_ROLE_ID      = 1530612426133868576
LOG_CHANNEL_ID        = 1531494819149644038
ACCESS_REQ_CHANNEL_ID = 1531496471449440336
START_HERE_CHANNEL_ID = 1531496467154468955
WEEKLY_PUBLIC_CH_ID   = 1531496475379499222

TOKEN   = os.environ.get("DISCORD_TOKEN")
PACIFIC = ZoneInfo("America/Los_Angeles")

SCHEDULE = {0: dt.time(8, 30), 1: dt.time(9, 0), 2: dt.time(9, 0),
            3: dt.time(8, 30), 4: dt.time(9, 0), 5: dt.time(9, 0), 6: None}
# Per-day end of the call session (early-leave cutoff & daily report time).
END_BY_DAY  = {0: dt.time(18, 0), 1: dt.time(18, 0), 2: dt.time(18, 0),
               3: dt.time(18, 0), 4: dt.time(18, 0), 5: dt.time(14, 0)}  # Sat ends 2 PM
WEEKLY_TIME = dt.time(10, 0)
WEEKLY_DAY  = 5                 # Saturday

FLAG_LATE   = 3
FLAG_EARLY  = 2
CAMERA_MIN_PCT   = 0.50         # below this % camera-on = flagged
CAMERA_MIN_MINS  = 30           # only judge camera once present this many minutes

EXCLUDE_NAME_CONTAINS     = ["join to create"]
EXCLUDE_CATEGORY_CONTAINS = ["statdock"]
STATE_FILE   = "attendance_state.json"
HISTORY_FILE = "attendance_history.json"

# --- Wins feed: auto-post new sales from the Supabase deals table to #wins ---
WINS_CHANNEL_ID = 1531689406832836719
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://pgxoyhlcbjuoucvubsmy.supabase.co").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")   # tracker's Supabase anon key (set in Railway)
WINS_STATE_FILE = "wins_state.json"

# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
client = discord.Client(intents=intents)

today = {}
current_day = None


def now_pt():           return dt.datetime.now(PACIFIC)
def today_key():        return now_pt().date().isoformat()
def scheduled_start_today(): return SCHEDULE.get(now_pt().weekday())
def end_today(): return END_BY_DAY.get(now_pt().weekday())

def fmt(x):
    if x is None: return "—"
    d = x if isinstance(x, dt.datetime) else dt.datetime.fromisoformat(x)
    return d.astimezone(PACIFIC).strftime("%-I:%M %p")

def hstr(sec): return f"{sec/3600.0:.1f}h"

def load_json(p, d):
    if os.path.exists(p):
        try:
            with open(p) as f: return json.load(f)
        except Exception as e: print("load", p, e)
    return d

def save_json(p, d):
    try:
        with open(p, "w") as f: json.dump(d, f)
    except Exception as e: print("save", p, e)

def save_state(): save_json(STATE_FILE, {"day": current_day, "today": today})

def load_state():
    global today, current_day
    data = load_json(STATE_FILE, {})
    if data.get("day") == today_key():
        current_day = data["day"]; today = data["today"]

def is_work_channel(ch):
    if ch is None: return False
    g = ch.guild
    if g.afk_channel and ch.id == g.afk_channel.id: return False
    n = ch.name.lower()
    if any(s in n for s in EXCLUDE_NAME_CONTAINS): return False
    cat = ch.category.name.lower() if ch.category else ""
    if any(s in cat for s in EXCLUDE_CATEGORY_CONTAINS): return False
    return True

def _open(base, ts_key, rec):
    total = rec.get(base, 0.0)
    if rec.get(ts_key):
        total += (now_pt() - dt.datetime.fromisoformat(rec[ts_key])).total_seconds()
    return total

def live_seconds(rec): return _open("total_seconds", "enter_ts", rec)
def live_camera(rec):  return _open("camera_seconds", "cam_ts", rec)

def camera_pct(rec):
    s = live_seconds(rec)
    return (live_camera(rec) / s) if s > 0 else 0.0

def ensure_today():
    global today, current_day
    if current_day != today_key():
        current_day = today_key(); today = {}; save_state()

def is_early_leave(rec):
    if rec["present"]: return False
    ll = rec["last_leave"]
    if not ll: return True
    cutoff = end_today() or dt.time(18, 0)
    return dt.datetime.fromisoformat(ll).astimezone(PACIFIC).time() < cutoff


# ---- voice + camera tracking ---------------------------------------------
@client.event
async def on_voice_state_update(member, before, after):
    if member.bot: return
    ensure_today()
    was, now = is_work_channel(before.channel), is_work_channel(after.channel)
    ts = now_pt(); mid = str(member.id); rec = today.get(mid)

    if now and not was:                                   # entered a work room
        if rec is None:
            start = scheduled_start_today()
            late = start is not None and ts.time() > start
            rec = {"name": member.display_name, "first_join": ts.isoformat(),
                   "last_leave": None, "enter_ts": ts.isoformat(), "total_seconds": 0.0,
                   "present": True, "late": late, "camera_on": False, "cam_ts": None,
                   "camera_seconds": 0.0}
            today[mid] = rec
            await announce_arrival(member, ts, late, start)
        else:
            rec["present"] = True; rec["last_leave"] = None
            if not rec.get("enter_ts"): rec["enter_ts"] = ts.isoformat()
        if after.self_video and not rec["camera_on"]:
            rec["camera_on"] = True; rec["cam_ts"] = ts.isoformat()
        save_state()
    elif was and not now:                                 # left all work rooms
        if rec:
            if rec.get("camera_on") and rec.get("cam_ts"):
                rec["camera_seconds"] = live_camera(rec)
            rec["camera_on"] = False; rec["cam_ts"] = None
            if rec.get("enter_ts"): rec["total_seconds"] = live_seconds(rec)
            rec["enter_ts"] = None; rec["present"] = False; rec["last_leave"] = ts.isoformat()
            save_state()
    elif now and was and rec:                             # same room — camera toggle
        cam = bool(after.self_video)
        if cam and not rec["camera_on"]:
            rec["camera_on"] = True; rec["cam_ts"] = ts.isoformat(); save_state()
        elif not cam and rec["camera_on"]:
            rec["camera_seconds"] = live_camera(rec); rec["camera_on"] = False
            rec["cam_ts"] = None; save_state()

async def announce_arrival(member, ts, late, start):
    ch = client.get_channel(LOG_CHANNEL_ID)
    if not ch: return
    msg = (f"🔴 **LATE** — {member.mention} clocked in at **{fmt(ts)}** "
           f"(start was {start.strftime('%-I:%M %p')})." if late
           else f"🟢 {member.mention} clocked in at **{fmt(ts)}** — on time.")
    try: await ch.send(msg)
    except Exception as e: print("announce", e)


# ---- history / flags ------------------------------------------------------
def snapshot_today():
    guild = client.get_guild(GUILD_ID)
    start = scheduled_start_today(); day = today_key(); snap = {}
    for mid, rec in today.items():
        snap[mid] = {"name": rec["name"], "seconds": live_seconds(rec),
            "camera_seconds": live_camera(rec), "late": rec["late"],
            "left_early": is_early_leave(rec), "no_show": False}
    if guild and start is not None:
        role = guild.get_role(VERIFIED_ROLE_ID)
        if role:
            for m in role.members:
                if not m.bot and str(m.id) not in snap:
                    snap[str(m.id)] = {"name": m.display_name, "seconds": 0,
                        "camera_seconds": 0, "late": False, "left_early": False, "no_show": True}
    hist = load_json(HISTORY_FILE, {}); hist[day] = snap; save_json(HISTORY_FILE, hist)

def trailing_counts(days=7):
    hist = load_json(HISTORY_FILE, {}); end = now_pt().date(); counts = {}
    for i in range(days):
        for mid, r in hist.get((end - dt.timedelta(days=i)).isoformat(), {}).items():
            c = counts.setdefault(mid, {"name": r["name"], "late": 0, "early": 0})
            c["name"] = r["name"]
            if r.get("late"): c["late"] += 1
            if r.get("left_early"): c["early"] += 1
    for mid, rec in today.items():
        c = counts.setdefault(mid, {"name": rec["name"], "late": 0, "early": 0})
        if rec["late"]: c["late"] += 1
        if is_early_leave(rec): c["early"] += 1
    return counts


# ---- daily report ---------------------------------------------------------
async def post_daily_report():
    ch = client.get_channel(LOG_CHANNEL_ID); start = scheduled_start_today()
    if not ch or start is None: return
    guild = client.get_guild(GUILD_ID)
    late, early, full, absent, lowcam, violators = [], [], [], [], [], []
    for mid, rec in today.items():
        secs = live_seconds(rec); le = is_early_leave(rec); cp = camera_pct(rec)
        if rec["late"]: late.append(f"• {rec['name']} — arrived **{fmt(rec['first_join'])}**")
        if le:          early.append(f"• {rec['name']} — left **{fmt(rec['last_leave'])}** ({hstr(secs)})")
        if not rec["late"] and not le: full.append(f"• {rec['name']} — {hstr(secs)}")
        if secs >= CAMERA_MIN_MINS*60 and cp < CAMERA_MIN_PCT:
            lowcam.append(f"• {rec['name']} — camera on **{cp*100:.0f}%** of {hstr(secs)}")
        pts = (1 if rec["late"] else 0) + (1 if le else 0)
        if pts: violators.append((pts, rec["name"]))
    role = guild.get_role(VERIFIED_ROLE_ID) if guild else None
    if role:
        for m in role.members:
            if not m.bot and str(m.id) not in today: absent.append(f"• {m.display_name}")
    violators.sort(reverse=True)
    end_t = end_today() or dt.time(18, 0)
    e = discord.Embed(title=f"📋 Attendance — {now_pt().strftime('%A, %b %-d')}",
        description=f"Start **{start.strftime('%-I:%M %p')} PT** · End **{end_t.strftime('%-I:%M %p')} PT**",
        color=0xE23B3B if (late or early or absent or lowcam) else 0x2ECC71)
    e.add_field(name=f"🔴 Late ({len(late)})", value="\n".join(late) or "None 🎉", inline=False)
    e.add_field(name=f"🟠 Left before {end_t.strftime('%-I:%M %p')} ({len(early)})", value="\n".join(early) or "None 🎉", inline=False)
    e.add_field(name=f"📷 Low camera — under {int(CAMERA_MIN_PCT*100)}% ({len(lowcam)})",
                value="\n".join(lowcam) or "None 🎉", inline=False)
    e.add_field(name=f"✅ Full day, on time ({len(full)})", value="\n".join(full) or "—", inline=False)
    if absent: e.add_field(name=f"⚫ No-show ({len(absent)})", value="\n".join(absent), inline=False)
    if violators:
        e.add_field(name="⚠️ Violation points (late + early)",
            value="\n".join(f"• {n} — {p} pt{'s' if p!=1 else ''}" for p, n in violators[:10]), inline=False)
    e.set_footer(text="Private · Pacific · AFK not counted")
    try: await ch.send(embed=e)
    except Exception as ex: print("daily", ex)
    snapshot_today()
    await post_autoflags(ch, guild)

async def post_autoflags(ch, guild):
    counts = trailing_counts(7)
    flagged = [c for c in counts.values() if c["late"] >= FLAG_LATE or c["early"] >= FLAG_EARLY]
    if not flagged: return
    flagged.sort(key=lambda c: c["late"] + c["early"], reverse=True)
    owner = f"<@{guild.owner_id}> " if guild and guild.owner_id else ""
    e = discord.Embed(title="🚨 AUTO-FLAG — repeat offenders",
        description="\n".join(f"• **{c['name']}** — {c['late']} late / {c['early']} early (7 days)" for c in flagged),
        color=0xC0392B)
    e.set_footer(text=f"Threshold: {FLAG_LATE}+ late or {FLAG_EARLY}+ early in 7 days")
    try: await ch.send(content=owner.strip() or None, embed=e)
    except Exception as ex: print("autoflag", ex)


# ---- weekly ---------------------------------------------------------------
def aggregate_week():
    hist = load_json(HISTORY_FILE, {}); end = now_pt().date(); agg = {}
    for i in range(7):
        day = (end - dt.timedelta(days=i)).isoformat(); snap = hist.get(day)
        if not snap: continue
        scheduled = SCHEDULE.get(dt.date.fromisoformat(day).weekday()) is not None
        for mid, r in snap.items():
            a = agg.setdefault(mid, {"name": r["name"], "hours": 0.0, "cam": 0.0, "scheduled": 0,
                "present": 0, "late": 0, "early": 0, "noshow": 0})
            a["name"] = r["name"]; a["hours"] += r.get("seconds", 0)/3600.0
            a["cam"] += r.get("camera_seconds", 0)/3600.0
            if scheduled:
                a["scheduled"] += 1
                if r.get("no_show"): a["noshow"] += 1
                else:
                    a["present"] += 1
                    if r.get("late"): a["late"] += 1
                    if r.get("left_early"): a["early"] += 1
    return agg

async def post_weekly():
    agg = aggregate_week()
    if not agg: return
    rows = list(agg.values())
    pub = client.get_channel(WEEKLY_PUBLIC_CH_ID)
    if pub:
        top = sorted(rows, key=lambda r: r["hours"], reverse=True)[:5]
        perfect = sorted([r for r in rows if r["scheduled"] and r["present"] == r["scheduled"]
                          and r["late"] == 0 and r["early"] == 0], key=lambda r: r["hours"], reverse=True)
        e = discord.Embed(title="🏆 Weekly Leaderboard",
            description=now_pt().strftime("Week ending %A, %b %-d"), color=0xF1C40F)
        e.add_field(name="🔥 Top Hours",
            value="\n".join(f"**{i+1}.** {r['name']} — {r['hours']:.1f}h" for i, r in enumerate(top)) or "—", inline=False)
        e.add_field(name="🎯 Perfect Week (on time + full, every scheduled day)",
            value="\n".join(f"• {r['name']} — {r['hours']:.1f}h" for r in perfect) or "Nobody yet — be the first.", inline=False)
        e.set_footer(text="Excel Financial · AFK not counted")
        try: await pub.send(embed=e)
        except Exception as ex: print("weekly pub", ex)
    priv = client.get_channel(LOG_CHANNEL_ID)
    if priv:
        def consist(r): return (r["present"] - r["late"] - r["early"]) / r["scheduled"] if r["scheduled"] else 0
        for r in rows: r["v"] = r["late"] + r["early"]
        violators = sorted([r for r in rows if r["v"] > 0], key=lambda r: r["v"], reverse=True)[:10]
        at_risk = sorted([r for r in rows if r["scheduled"] and consist(r) < 0.6], key=consist)
        lowcam = sorted([r for r in rows if r["hours"] > 1 and (r["cam"]/r["hours"]) < CAMERA_MIN_PCT],
                        key=lambda r: r["cam"]/r["hours"])
        e = discord.Embed(title="📊 Weekly Accountability (private)",
            description=now_pt().strftime("Week ending %A, %b %-d"), color=0xE23B3B)
        e.add_field(name="🚩 Top Violators (late + early)",
            value="\n".join(f"• {r['name']} — {r['v']} pts ({r['late']}L / {r['early']}E)" for r in violators) or "None 🎉", inline=False)
        e.add_field(name="📷 Low camera this week",
            value="\n".join(f"• {r['name']} — {(r['cam']/r['hours'])*100:.0f}% of {r['hours']:.1f}h" for r in lowcam) or "None 🎉", inline=False)
        e.add_field(name="⚠️ At-risk (consistency < 60%)",
            value="\n".join(f"• {r['name']} — {consist(r)*100:.0f}% ({r['present']}/{r['scheduled']} days, {r['noshow']} NS)" for r in at_risk) or "None 🎉", inline=False)
        e.set_footer(text="Owner eyes only · Pacific")
        try: await priv.send(embed=e)
        except Exception as ex: print("weekly priv", ex)


# ---- verification ---------------------------------------------------------
def request_button():
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(label="Request Access", style=discord.ButtonStyle.primary, custom_id="req_access", emoji="✅"))
    return v

def approve_deny_view(uid):
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(label="Approve", style=discord.ButtonStyle.success, custom_id=f"appr_{uid}", emoji="✅"))
    v.add_item(discord.ui.Button(label="Deny", style=discord.ButtonStyle.danger, custom_id=f"deny_{uid}", emoji="⛔"))
    return v

async def ensure_start_message():
    ch = client.get_channel(START_HERE_CHANNEL_ID)
    if not ch: return
    async for m in ch.history(limit=20):
        if m.author == client.user and m.embeds and m.embeds[0].title == "Welcome to Excel Financial":
            return
    e = discord.Embed(title="Welcome to Excel Financial",
        description=("Private team server. Click **Request Access** and an admin will approve you, "
                     "unlocking the voice rooms.\n\nCulture is everything — camera on, on time, all gas."),
        color=0x2ECC71)
    await ch.send(embed=e, view=request_button())

@client.event
async def on_interaction(interaction):
    if interaction.type != discord.InteractionType.component: return
    cid = (interaction.data or {}).get("custom_id", ""); guild = client.get_guild(GUILD_ID)
    if cid == "req_access":
        reqch = client.get_channel(ACCESS_REQ_CHANNEL_ID)
        if reqch:
            u = interaction.user
            e = discord.Embed(title="🛂 Access Request",
                description=f"{u.mention} (`{u}`) is requesting access.", color=0x3498DB,
                timestamp=dt.datetime.now(dt.timezone.utc))
            await reqch.send(embed=e, view=approve_deny_view(u.id))
        await interaction.response.send_message("✅ Request sent — an admin will approve you shortly.", ephemeral=True)
    elif cid.startswith("appr_") or cid.startswith("deny_"):
        uid = int(cid.split("_", 1)[1]); member = guild.get_member(uid) if guild else None
        approver = interaction.user
        if cid.startswith("appr_"):
            role = guild.get_role(VERIFIED_ROLE_ID)
            if member and role:
                try: await member.add_roles(role, reason=f"Approved by {approver}")
                except Exception as e: print("grant", e)
            txt = f"✅ Approved by {approver.mention}"
        else:
            txt = f"⛔ Denied by {approver.mention}"
        try: await interaction.response.edit_message(content=txt, embed=interaction.message.embeds[0], view=None)
        except Exception: await interaction.response.send_message(txt, ephemeral=True)


# ---- wins feed (Supabase deals -> #wins) ---------------------------------
async def fetch_recent_deals(limit=50):
    url = f"{SUPABASE_URL}/rest/v1/deals"
    headers = {"apikey": SUPABASE_KEY, "authorization": f"Bearer {SUPABASE_KEY}"}
    params = {"select": "*", "order": "posted_at.desc", "limit": str(limit)}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, headers=headers,
                         timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                print("deals fetch", r.status, (await r.text())[:200]); return []
            return await r.json()

def fmt_deal(d):
    agent = d.get("agent") or "Someone"
    parts = []
    if d.get("apps"):
        n = d["apps"]; parts.append(f"{n} app{'s' if n != 1 else ''}")
    if d.get("ap"):
        try: parts.append(f"${int(float(d['ap'])):,} AP")
        except Exception: parts.append(f"{d['ap']} AP")
    elif d.get("gross"):
        try: parts.append(f"${int(float(d['gross'])):,}")
        except Exception: pass
    line = f"💰 **{agent}** just closed"
    if parts: line += " — " + " · ".join(parts)
    if d.get("carrier"): line += f" ({d['carrier']})"
    return line + " 🔥"

@tasks.loop(seconds=90)
async def wins_poller():
    if not SUPABASE_KEY: return
    ch = client.get_channel(WINS_CHANNEL_ID)
    if not ch: return
    try:
        deals = await fetch_recent_deals(50)
    except Exception as e:
        print("wins poll error", e); return
    data = load_json(WINS_STATE_FILE, {"seen": [], "init": False})
    seen = set(str(x) for x in data.get("seen", []))
    init = data.get("init", False)
    order = sorted(deals, key=lambda d: d.get("posted_at") or "")  # oldest first
    for d in order:
        did = str(d.get("id"))
        if did in seen: continue
        if init:
            try: await ch.send(fmt_deal(d))
            except Exception as e: print("wins send", e)
        seen.add(did)
    seen_list = list(seen)[-1000:]
    save_json(WINS_STATE_FILE, {"seen": seen_list, "init": True})


# ---- scheduler ------------------------------------------------------------
_last_daily = None
_last_weekly = None

@client.event
async def on_ready():
    print(f"Logged in as {client.user}.")
    load_state(); await ensure_start_message()
    if not scheduler.is_running(): scheduler.start()
    if SUPABASE_KEY and not wins_poller.is_running(): wins_poller.start()

@tasks.loop(seconds=30)
async def scheduler():
    global _last_daily, _last_weekly
    n = now_pt(); ensure_today()
    if end_today() and n.time() >= end_today() and _last_daily != n.date() and scheduled_start_today() is not None:
        _last_daily = n.date(); await post_daily_report()
    if n.weekday() == WEEKLY_DAY and n.time() >= WEEKLY_TIME and _last_weekly != n.date():
        _last_weekly = n.date(); await post_weekly()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set the DISCORD_TOKEN environment variable first (see README).")
    client.run(TOKEN)
