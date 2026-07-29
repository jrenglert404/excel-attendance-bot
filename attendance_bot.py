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
  IP REPORTS     — #ip-reports: Issued Premium scoreboard, posted when you drop a
                   month-to-date Gateway file into the tracker. WEEKLY drops show each
                   agent's production SINCE THE LAST DROP (new MTD − previous MTD), top 5,
                   auto-numbered "August — Week N IP Report". Tick "Final MTD" in the
                   tracker on the month's last drop to post the top-10 cumulative total.
                   The tracker computes the delta & week number; the bot just formats and
                   posts. No fixed schedule. Same SUPABASE_KEY as deals.
  LEAD ROI       — #lead-roi: reps log each lead ORDER (vendor/type/qty/price) via a button
                   + pop-up form; the bot keeps a rolling month-to-date tally. Weekly public
                   scoreboard shows spend / AP / IP / AP× / IP× per rep, ranked by AP written.
                   Private #lead-report gives the owner a by-type & by-vendor breakdown of
                   what's being bought. Totals only in public — individual orders stay private.
                   Needs a small Supabase table (setup SQL in README / lead_spend_setup.sql).
  QUESTS         — #recognition: one rotating RESULTS quest per week (AP / deals / apps /
                   big-case). Auto-tracked from the deals feed; live shoutout when a rep
                   clears it, recap with the Saturday recognition card.
  TEAM PRODUCTION— #team-production: two top-10 boards in one auto-updating post — a
                   MANAGER SCOREBOARD (each rep's downline rolled up their full upline
                   chain from the tracker; only managers whose downline has written
                   business appear) and an INDIVIDUAL SCOREBOARD (personal production).
                   Submitted-AP edits itself live all month; an issued-IP version posts at
                   month end (1st).

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
WINS_CHANNEL_ID = 1531689406832836719           # #deals
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://pgxoyhlcbjuoucvubsmy.supabase.co").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")   # tracker's Supabase anon key (set in Railway)
WINS_STATE_FILE = "wins_state.json"

# --- Recognition hub (#recognition — the repurposed #weekly-report channel) ---
RECOGNITION_CH_ID = 1531496475379499222
WEEKLY_APPS_GOAL = 150         # team's weekly apps goal — change to your real number
MILESTONE_DEALS = [25, 50, 75, 100, 150, 200, 300]
MILESTONE_AP    = [25000, 50000, 100000, 150000, 200000, 300000]
MILESTONE_FILE  = "milestone_state.json"

# --- IP Reports: Issued Premium scoreboard from the tracker's Gateway MTD imports ---
# Reads the same Supabase app_state blob your Goal & Commit Tracker syncs to. Each time
# you drop a month-to-date Gateway file, the TRACKER stages a report payload
# (state.ipReport) with that week's per-agent delta (new MTD − previous MTD) and an
# auto-incrementing week number; this bot detects it (within ~5 min) and posts to
# #ip-reports:
#   • a normal drop        -> "<Month> — Week N IP Report"   (top 5 by this week's issued)
#   • a "Final MTD" drop    -> "<Month> — Final MTD IP Report" (top 10 by month total)
# You mark the final one with the "Final MTD" checkbox in the tracker's import box.
# No fixed schedule. Uses the same SUPABASE_KEY / SUPABASE_URL as the deals feed.
IP_REPORTS_CH_ID = 1531721169722675361          # #ip-reports
IP_STATE_FILE    = "ip_state.json"
IP_WEEKLY_N      = 5            # top-N on a weekly (weeks 1-4) board
IP_MONTHLY_N     = 10           # top-N on the Final MTD (last week) board
IP_EXCLUDE       = {"jesse englert"}   # names kept off the public board (owner's own pen); lowercase

# --- Lead ROI board: reps log each lead ORDER (vendor/type/qty/price); the bot keeps a
#     rolling monthly tally. Public #lead-roi shows spend-vs-results totals; a private
#     owner report breaks down by lead type & vendor. ---
LEAD_ROI_CH_ID    = 1531853384309669960   # #lead-roi  (public totals scoreboard)
LEAD_REPORT_CH_ID = 1531859358479155220   # #lead-report (owner-only type/vendor breakdown)
LEAD_TABLE        = "lead_purchases"       # Supabase append-only log (see setup SQL in README)

# --- Team Production: manager leaderboard, each rep's downline rolled up the hierarchy
#     (uplines come from your tracker). Submitted-AP board auto-updates live all month;
#     issued-IP team board posts at month end. ---
TEAM_CH_ID = 1531861880824402000           # #team-production

# --- Weekly Quests: rotating RESULTS challenges, auto-tracked from the deals feed ---
QUEST_CH_ID     = RECOGNITION_CH_ID     # quests + shoutouts land in #recognition
QUEST_STATE_FILE = "quest_state.json"
# Every quest is results-only. One runs per ISO week, rotating in order. Anyone who
# clears the bar gets a live shoutout; a recap posts with the weekly recognition card.
QUESTS = [
    {"id": "ap10k",  "emoji": "💵", "title": "$10K AP Week",   "metric": "ap",      "goal": 10000,
     "desc": "Write **$10,000+ submitted AP** this week."},
    {"id": "deals5", "emoji": "🔥", "title": "5-Deal Week",    "metric": "deals",   "goal": 5,
     "desc": "Close **5+ deals** this week."},
    {"id": "big3k",  "emoji": "🐘", "title": "Big Case Bounty", "metric": "maxdeal", "goal": 3000,
     "desc": "Close a **single policy worth $3,000+ AP** this week."},
    {"id": "apps15", "emoji": "⚡", "title": "15-App Week",     "metric": "apps",    "goal": 15,
     "desc": "Submit **15+ apps** this week."},
    {"id": "ap20k",  "emoji": "🚀", "title": "$20K AP Club",    "metric": "ap",      "goal": 20000,
     "desc": "Write **$20,000+ submitted AP** this week."},
]
QUEST_BY_ID = {q["id"]: q for q in QUESTS}

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

def bar(pct, width=14):
    pct = max(0.0, min(1.0, pct))
    f = int(round(pct * width))
    return "█" * f + "░" * (width - f)

async def post_weekly():
    agg = aggregate_week()
    rows = list(agg.values())
    deals = []
    if SUPABASE_KEY:
        start = (now_pt() - dt.timedelta(days=7)).astimezone(dt.timezone.utc).isoformat()
        try: deals = await fetch_deals_since(start)
        except Exception as e: print("recog deals", e)
    ds = summarize_deals(deals)

    # ---- PUBLIC recognition -> #recognition ----
    rec = client.get_channel(RECOGNITION_CH_ID)
    if rec:
        e = discord.Embed(title="🏅 Weekly Recognition",
            description=now_pt().strftime("Week ending %A, %b %-d"), color=0xF1C40F)
        pct = (ds["apps"] / WEEKLY_APPS_GOAL) if WEEKLY_APPS_GOAL else 0
        e.add_field(name="🎯 Team Goal",
            value=f"**{ds['apps']} / {WEEKLY_APPS_GOAL} apps**\n`{bar(pct)}` {pct*100:.0f}%", inline=False)
        e.add_field(name=f"🏆 Top Closers — {ds['count']} deals · ${int(ds['ap']):,} AP",
            value=leaderboard_lines(ds["by"], 5), inline=False)
        top = sorted(rows, key=lambda r: r["hours"], reverse=True)[:5]
        e.add_field(name="⏱️ Top Hours",
            value="\n".join(f"**{i+1}.** {r['name']} — {r['hours']:.1f}h" for i, r in enumerate(top)) or "—",
            inline=False)
        deal_names = set(str(n).strip().lower() for n in ds["by"])
        warm = [r["name"] for r in rows if r["hours"] >= 5 and r["name"].strip().lower() not in deal_names]
        if warm:
            e.add_field(name="🪑 On the clock, no deals yet",
                value="\n".join("• " + n for n in warm[:10]), inline=False)
        e.set_footer(text="Excel Financial · hours exclude AFK")
        try: await rec.send(embed=e)
        except Exception as ex: print("recognition", ex)

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
    elif cid == "lead_spend_submit":
        try: await interaction.response.send_modal(LeadOrderModal())
        except Exception as e: print("lead modal", e)
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
    new_posted = False
    for d in order:
        did = str(d.get("id"))
        if did in seen: continue
        if init:
            try: await ch.send(fmt_deal(d)); new_posted = True
            except Exception as e: print("wins send", e)
        seen.add(did)
    seen_list = list(seen)[-1000:]
    save_json(WINS_STATE_FILE, {"seen": seen_list, "init": True})
    if new_posted:
        await check_milestones()
        await check_quest()
        await refresh_team_ap_board()   # live team-production board follows new deals

async def check_milestones():
    rec = client.get_channel(RECOGNITION_CH_ID)
    if not rec: return
    start = (now_pt() - dt.timedelta(days=7)).astimezone(dt.timezone.utc).isoformat()
    try: deals = await fetch_deals_since(start)
    except Exception: return
    s = summarize_deals(deals)
    wk = now_pt().strftime("%Y-W%W")
    st = load_json(MILESTONE_FILE, {})
    hit = st.get(wk, {"deals": [], "ap": []})
    changed = False
    for m in MILESTONE_DEALS:
        if s["count"] >= m and m not in hit["deals"]:
            hit["deals"].append(m); changed = True
            try: await rec.send(f"🎉 **{m} deals** closed this week and climbing — let's go! 🔥")
            except Exception as e: print("milestone", e)
    for m in MILESTONE_AP:
        if s["ap"] >= m and m not in hit["ap"]:
            hit["ap"].append(m); changed = True
            try: await rec.send(f"💥 **${m:,} AP** this week! Momentum is real. 🚀")
            except Exception as e: print("milestone", e)
    if changed:
        st[wk] = hit; save_json(MILESTONE_FILE, st)


# ---- deals totals (daily + weekly summary in #deals) ----------------------
async def fetch_deals_since(iso):
    url = f"{SUPABASE_URL}/rest/v1/deals"
    headers = {"apikey": SUPABASE_KEY, "authorization": f"Bearer {SUPABASE_KEY}"}
    params = {"select": "*", "posted_at": f"gte.{iso}", "order": "posted_at.asc"}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, headers=headers,
                         timeout=aiohttp.ClientTimeout(total=25)) as r:
            if r.status != 200:
                print("deals summary fetch", r.status, (await r.text())[:200]); return []
            return await r.json()

def _num(x):
    try: return float(x)
    except Exception: return 0.0

def summarize_deals(deals):
    by = {}
    for d in deals:
        a = d.get("agent") or "Unknown"
        e = by.setdefault(a, {"deals": 0, "apps": 0, "ap": 0.0})
        e["deals"] += 1; e["apps"] += int(_num(d.get("apps"))); e["ap"] += _num(d.get("ap"))
    return {"count": len(deals),
            "apps": sum(int(_num(d.get("apps"))) for d in deals),
            "ap": sum(_num(d.get("ap")) for d in deals), "by": by}

def leaderboard_lines(by, n=5):
    rows = sorted(by.items(), key=lambda kv: kv[1]["ap"], reverse=True)[:n]
    medals = ["🥇", "🥈", "🥉"]
    out = []
    for i, (name, s) in enumerate(rows):
        tag = medals[i] if i < 3 else f"**{i+1}.**"
        out.append(f"{tag} {name} — {s['deals']} deal{'s' if s['deals'] != 1 else ''} · ${int(s['ap']):,}")
    return "\n".join(out) or "—"

async def post_deals_daily():
    if not SUPABASE_KEY: return
    ch = client.get_channel(WINS_CHANNEL_ID)
    if not ch: return
    start = now_pt().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc).isoformat()
    try: deals = await fetch_deals_since(start)
    except Exception as e: print("deals daily", e); return
    s = summarize_deals(deals)
    e = discord.Embed(title=f"📊 Deals Today — {now_pt().strftime('%A, %b %-d')}",
        description=f"**{s['count']}** deals · **{s['apps']}** apps · **${int(s['ap']):,} AP**",
        color=0xF1C40F)
    if s["by"]: e.add_field(name="Top closers", value=leaderboard_lines(s["by"], 5), inline=False)
    try: await ch.send(embed=e)
    except Exception as ex: print("deals daily send", ex)

async def post_deals_weekly():
    if not SUPABASE_KEY: return
    ch = client.get_channel(WINS_CHANNEL_ID)
    if not ch: return
    start = (now_pt() - dt.timedelta(days=7)).astimezone(dt.timezone.utc).isoformat()
    try: deals = await fetch_deals_since(start)
    except Exception as e: print("deals weekly", e); return
    s = summarize_deals(deals)
    e = discord.Embed(title=f"🏆 Deals This Week — ending {now_pt().strftime('%A, %b %-d')}",
        description=f"**{s['count']}** deals · **{s['apps']}** apps · **${int(s['ap']):,} AP**",
        color=0xE67E22)
    if s["by"]: e.add_field(name="🔥 Top Closers", value=leaderboard_lines(s["by"], 10), inline=False)
    try: await ch.send(embed=e)
    except Exception as ex: print("deals weekly send", ex)


# ---- IP Reports (Issued Premium scoreboard from the tracker) --------------
async def fetch_app_state():
    """Pull the tracker's full state blob from Supabase app_state (id=main)."""
    url = f"{SUPABASE_URL}/rest/v1/app_state"
    headers = {"apikey": SUPABASE_KEY, "authorization": f"Bearer {SUPABASE_KEY}"}
    params = {"id": "eq.main", "select": "data"}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, headers=headers,
                         timeout=aiohttp.ClientTimeout(total=25)) as r:
            if r.status != 200:
                print("app_state fetch", r.status, (await r.text())[:200]); return None
            rows = await r.json()
    if not rows or not isinstance(rows, list): return None
    data = rows[0].get("data")
    return data if isinstance(data, dict) else None

def _month_label(mkey):
    try:
        y, m = mkey.split("-");
        return dt.date(int(y), int(m), 1).strftime("%B %Y")
    except Exception:
        return mkey

def _filter_agents(state, raw, positive_only=True):
    """Roster-only map with the owner and off-roster ghosts removed. Used for both the
       cumulative MTD (a month's net) and the weekly delta the tracker computes."""
    roster = set(str(n).strip().lower() for n in (state.get("roster") or []))
    out = {}
    for agent, v in (raw or {}).items():
        nm = str(agent).strip()
        low = nm.lower()
        if low in IP_EXCLUDE: continue
        if roster and low not in roster: continue      # keep quitters/ghosts off the public board
        val = _num(v)
        if positive_only and val <= 0: continue
        out[nm] = val
    return out

def _net_map(state, mkey):
    """{agent: cumulative issued IP} for one month, roster-only, owner & ghosts removed."""
    months = state.get("months") or {}
    return _filter_agents(state, (months.get(mkey) or {}).get("net") or {})

def _months_with_ip(state):
    """Sorted list of month keys (oldest->newest) that have any qualifying IP."""
    months = state.get("months") or {}
    keys = [k for k in months.keys() if _net_map(state, k)]
    return sorted(keys)

def ip_leaderboard(net_map, n):
    rows = sorted(net_map.items(), key=lambda kv: kv[1], reverse=True)[:n]
    medals = ["🥇", "🥈", "🥉"]
    out = []
    for i, (name, v) in enumerate(rows):
        tag = medals[i] if i < 3 else f"**{i+1}.**"
        out.append(f"{tag} {name} — **${int(round(v)):,}**")
    return "\n".join(out) or "—"

def ip_signature(state):
    """Fingerprint that changes each time the tracker records a new IP report (every
       Gateway drop stamps state.ipReport.at), so each drop posts exactly once."""
    rpt = state.get("ipReport") or {}
    return json.dumps({"at": rpt.get("at"), "m": rpt.get("month"), "lbl": rpt.get("label")},
                      sort_keys=True)

async def post_ip_report():
    """Post the IP board the tracker just staged in state.ipReport.
       Weekly drop  -> top-5 by each agent's production SINCE THE LAST DROP (the delta).
       Final MTD    -> top-10 by the month's cumulative issued total.
       Week number and the final flag are decided in the tracker (auto-count + checkbox)."""
    ch = client.get_channel(IP_REPORTS_CH_ID)
    if not ch: return
    state = await fetch_app_state()
    if not state:
        print("ip report: no state"); return
    rpt = state.get("ipReport") or {}
    mkey = rpt.get("month")
    if not mkey:
        print("ip report: no ipReport payload (old tracker?)"); return
    label = _month_label(mkey)                         # "August 2026"
    mname = label.split(" ")[0]                         # "August"
    fname = rpt.get("file")
    if rpt.get("final"):
        nm = _net_map(state, mkey)                     # cumulative MTD
        if not nm: return
        n = IP_MONTHLY_N; total = sum(nm.values())
        board = ip_leaderboard(nm, n)
        title = f"🏆 {mname} — Final MTD IP Report"
        desc  = f"Month-to-date: **${int(round(total)):,}** issued · top {min(n, len(nm))}"
        field = "Issued Premium — month total"
        color = 0xE67E22
    else:
        wk = rpt.get("week")
        weekly = _filter_agents(state, rpt.get("weekly") or {})   # production since last drop
        n = IP_WEEKLY_N; total = sum(weekly.values())
        board = ip_leaderboard(weekly, n)
        wtxt = f"Week {wk}" if wk else "Weekly"
        title = f"📈 {mname} — {wtxt} IP Report"
        if weekly:
            desc = f"Issued since last drop: **${int(round(total)):,}** · top {min(n, len(weekly))}"
        else:
            desc = "No new issued production since the last drop."
        field = "Issued Premium — this week"
        color = 0x5865F2
    e = discord.Embed(title=title, description=desc, color=color)
    e.add_field(name=field, value=board, inline=False)
    foot = "Excel Financial · from your Gateway MTD import"
    if fname: foot += f" ({fname})"
    e.set_footer(text=foot)
    try: await ch.send(embed=e)
    except Exception as ex: print("ip report send", ex)

@tasks.loop(minutes=5)
async def ip_poller():
    """Watch the tracker for a new Gateway import (net data changed) and post the board."""
    if not SUPABASE_KEY: return
    if not client.get_channel(IP_REPORTS_CH_ID): return
    try:
        state = await fetch_app_state()
    except Exception as e:
        print("ip poll error", e); return
    if not state: return
    sig = ip_signature(state)
    st = load_json(IP_STATE_FILE, {"sig": None, "init": False})
    if not st.get("init"):
        st["init"] = True; st["sig"] = sig; save_json(IP_STATE_FILE, st); return  # no history spam on first boot
    if sig != st.get("sig"):
        st["sig"] = sig; save_json(IP_STATE_FILE, st)
        await post_ip_report()


# ---- Lead ROI board -------------------------------------------------------
def _live_month_key():
    n = now_pt()
    return f"{n.year:04d}-{n.month:02d}"

def _kfmt(v):
    v = float(v or 0)
    if abs(v) >= 1000: return f"${v/1000:.1f}k"
    return f"${v:.0f}"

def match_roster(state, name):
    """Best-effort map a typed name to a canonical roster name."""
    roster = list(state.get("roster") or [])
    q = " ".join(str(name or "").split()).strip().lower()
    if not q: return None
    low = {r.lower(): r for r in roster}
    if q in low: return low[q]
    for r in roster:                                   # startswith / substring
        if r.lower().startswith(q) or q in r.lower(): return r
    firsts = {}
    for r in roster:
        firsts.setdefault(r.split()[0].lower(), []).append(r)
    qf = q.split()[0]
    if qf in firsts and len(firsts[qf]) == 1: return firsts[qf][0]
    return None

async def fetch_lead_purchases(month):
    """Every lead-order row logged this month (append-only)."""
    url = f"{SUPABASE_URL}/rest/v1/{LEAD_TABLE}"
    headers = {"apikey": SUPABASE_KEY, "authorization": f"Bearer {SUPABASE_KEY}"}
    params = {"month": f"eq.{month}", "select": "agent,vendor,lead_type,quantity,price,discord_id"}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, headers=headers,
                         timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                print("lead fetch", r.status, (await r.text())[:200]); return []
            return await r.json() or []

async def insert_lead_purchase(month, agent, vendor, lead_type, quantity, price, discord_id):
    """Append one lead order — no overwrite; the month's rows sum into a rolling tally."""
    url = f"{SUPABASE_URL}/rest/v1/{LEAD_TABLE}"
    headers = {"apikey": SUPABASE_KEY, "authorization": f"Bearer {SUPABASE_KEY}",
               "content-type": "application/json", "Prefer": "return=minimal"}
    body = {"month": month, "agent": agent, "vendor": vendor, "lead_type": lead_type,
            "quantity": quantity, "price": price, "discord_id": str(discord_id),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers,
                          timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status not in (200, 201, 204):
                print("lead insert", r.status, (await r.text())[:200]); return False
            return True

def _agent_lead_totals(rows):
    """{agent: {spend, leads, orders}} — the rolling monthly tally per rep."""
    by = {}
    for row in rows or []:
        a = str(row.get("agent"))
        e = by.setdefault(a, {"spend": 0.0, "leads": 0, "orders": 0})
        e["spend"] += _num(row.get("price")); e["leads"] += int(_num(row.get("quantity"))); e["orders"] += 1
    return by

class LeadOrderModal(discord.ui.Modal, title="Log a Lead Order"):
    who = discord.ui.TextInput(label="Your name (as on the roster)",
        placeholder="First Last", required=True, max_length=60)
    vendor = discord.ui.TextInput(label="Lead vendor",
        placeholder="e.g. Need-A-Lead, Redbird, iLeads", required=True, max_length=40)
    lead_type = discord.ui.TextInput(label="Lead type",
        placeholder="e.g. Final Expense, Mortgage Protection, Aged", required=True, max_length=40)
    quantity = discord.ui.TextInput(label="Quantity of leads",
        placeholder="e.g. 50", required=True, max_length=8)
    price = discord.ui.TextInput(label="Total price spent ($)",
        placeholder="e.g. 500", required=True, max_length=12)

    async def on_submit(self, interaction: discord.Interaction):
        try: await interaction.response.defer(ephemeral=True)
        except Exception: pass
        async def reply(msg):
            try: await interaction.followup.send(msg, ephemeral=True)
            except Exception as e: print("lead reply", e)
        praw = str(self.price.value).replace("$", "").replace(",", "").strip()
        try: price = float(praw)
        except Exception: return await reply("⚠️ I couldn't read the price — just the number, e.g. `500`.")
        if price < 0: return await reply("⚠️ Price can't be negative.")
        qraw = str(self.quantity.value).replace(",", "").strip()
        try: qty = int(float(qraw))
        except Exception: return await reply("⚠️ I couldn't read the quantity — a whole number, e.g. `50`.")
        if qty < 0: return await reply("⚠️ Quantity can't be negative.")
        state = await fetch_app_state()
        if not state: return await reply("⚠️ Couldn't reach the roster right now — try again in a minute.")
        canon = match_roster(state, str(self.who.value))
        if not canon:
            return await reply(f"⚠️ I couldn't match \"{self.who.value}\" to the roster. Use your full name exactly as it appears on the tracker.")
        vendor = " ".join(str(self.vendor.value).split()).strip()
        ltype = " ".join(str(self.lead_type.value).split()).strip()
        month = _live_month_key()
        ok = await insert_lead_purchase(month, canon, vendor, ltype, qty, price, interaction.user.id)
        if not ok:
            return await reply("⚠️ Something went wrong saving that order. Ping the owner if it keeps happening.")
        mine = _agent_lead_totals(await fetch_lead_purchases(month)).get(canon, {"spend": price, "leads": qty, "orders": 1})
        await reply(
            f"✅ Logged **{qty} {ltype} leads** from **{vendor}** — **${int(round(price)):,}**.\n"
            f"📊 **{canon}** month-to-date: **${int(round(mine['spend'])):,}** · **{mine['orders']} orders** · **{mine['leads']} leads**.\n"
            f"Only monthly totals are public — your individual orders stay private.")

def lead_button():
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(label="Log a lead order", style=discord.ButtonStyle.success,
                                 custom_id="lead_spend_submit", emoji="💸"))
    return v

async def ensure_lead_roi_message():
    ch = client.get_channel(LEAD_ROI_CH_ID)
    if not ch: return
    e = discord.Embed(title="💸 Log your lead orders",
        description=("Bought leads? Hit the button and log the order — **vendor, type, quantity, price**. "
                     "Do it every time you buy; the bot keeps your **rolling month-to-date total**.\n\n"
                     "Only **you** see each order. The public scoreboard shows **monthly totals only** — never "
                     "individual orders."),
        color=0x1ABC9C)
    async for m in ch.history(limit=20):
        if m.author == client.user and m.components:   # refresh the existing button message in place
            try: await m.edit(embed=e, view=lead_button())
            except Exception as ex: print("lead msg edit", ex)
            return
    await ch.send(embed=e, view=lead_button())

async def post_lead_roi():
    if not SUPABASE_KEY: return
    ch = client.get_channel(LEAD_ROI_CH_ID)
    if not ch: return
    state = await fetch_app_state()
    if not state: return
    month = _live_month_key(); label = _month_label(month)
    totals = _agent_lead_totals(await fetch_lead_purchases(month))
    spenders = {a: t for a, t in totals.items() if t["spend"] > 0}
    if not spenders: return                            # nobody logged yet — no empty board
    mstart = now_pt().replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc).isoformat()
    try: deals = await fetch_deals_since(mstart)
    except Exception as e: print("lead roi deals", e); deals = []
    byap = summarize_deals(deals)["by"]                # {agent:{deals,apps,ap}}
    ipmap = _net_map(state, month)                     # {agent: issued IP}
    rows = []
    for agent, t in spenders.items():
        if str(agent).lower() in IP_EXCLUDE: continue
        spend = t["spend"]; ap = byap.get(agent, {}).get("ap", 0.0); ipv = ipmap.get(agent, 0.0)
        rows.append({"a": agent, "spend": spend, "ap": ap, "ip": ipv,
                     "apx": (ap / spend) if spend else 0.0, "ipx": (ipv / spend) if spend else 0.0})
    if not rows: return
    rows.sort(key=lambda r: r["ap"], reverse=True)     # RESULTS first — biggest writers lead
    head = f"{'#':<2}{'Rep':<13}{'Spend':>7}{'AP':>8}{'IP':>8}{'AP×':>6}{'IP×':>6}"
    lines = [head, "─" * len(head)]
    for i, r in enumerate(rows, 1):
        nm = r["a"].split()[0][:12]
        lines.append(f"{i:<2}{nm:<13}{_kfmt(r['spend']):>7}{_kfmt(r['ap']):>8}{_kfmt(r['ip']):>8}"
                     f"{r['apx']:>5.1f}x{r['ipx']:>5.1f}x")
    table = "```\n" + "\n".join(lines) + "\n```"
    e = discord.Embed(title=f"💸 Lead ROI Scoreboard — {label}",
        description=f"Month-to-date · ranked by AP written · **{len(rows)}** reps reporting\n{table}",
        color=0x1ABC9C)
    by_spend = sorted(rows, key=lambda r: r["spend"], reverse=True)
    top = by_spend[:3]
    team_ap = sum(r["ap"] for r in rows) or 1.0
    top_ap = sum(r["ap"] for r in top)
    e.add_field(name="💡 Spending works",
        value=(f"Your top {len(top)} lead investors wrote **${int(round(top_ap)):,}** — "
               f"**{top_ap/team_ap*100:.0f}%** of this board's AP. The reps who spend the most write the most."),
        inline=False)
    e.set_footer(text="Excel Financial · AP = submitted (live) · IP = issued from Gateway · totals only")
    try: await ch.send(embed=e)
    except Exception as ex: print("lead roi send", ex)

async def post_lead_report():
    """Owner-only breakdown of WHAT the team is buying — by lead type and by vendor."""
    if not SUPABASE_KEY: return
    ch = client.get_channel(LEAD_REPORT_CH_ID)
    if not ch: return
    month = _live_month_key(); label = _month_label(month)
    purchases = await fetch_lead_purchases(month)
    if not purchases: return
    total_spend = sum(_num(r.get("price")) for r in purchases)
    total_leads = sum(int(_num(r.get("quantity"))) for r in purchases)
    bytype, byvendor = {}, {}
    for r in purchases:
        t = (str(r.get("lead_type") or "").strip() or "—")
        v = (str(r.get("vendor") or "").strip() or "—")
        et = bytype.setdefault(t, {"spend": 0.0, "leads": 0})
        et["spend"] += _num(r.get("price")); et["leads"] += int(_num(r.get("quantity")))
        ev = byvendor.setdefault(v, {"spend": 0.0, "leads": 0})
        ev["spend"] += _num(r.get("price")); ev["leads"] += int(_num(r.get("quantity")))
    def _cpl(d): return (d["spend"] / d["leads"]) if d["leads"] else 0.0
    tden = total_spend or 1.0
    type_lines = [f"• **{t}** — ${int(round(d['spend'])):,} ({d['spend']/tden*100:.0f}%) · "
                  f"{d['leads']} leads · ${_cpl(d):.0f}/lead"
                  for t, d in sorted(bytype.items(), key=lambda kv: kv[1]["spend"], reverse=True)[:12]]
    vend_lines = [f"• **{v}** — ${int(round(d['spend'])):,} · {d['leads']} leads · ${_cpl(d):.0f}/lead"
                  for v, d in sorted(byvendor.items(), key=lambda kv: kv[1]["spend"], reverse=True)[:12]]
    e = discord.Embed(title=f"🧾 Lead Buying Report — {label}",
        description=f"**${int(round(total_spend)):,}** spent · **{total_leads:,} leads** · **{len(purchases)} orders** (month-to-date)",
        color=0xE67E22)
    e.add_field(name="By lead type", value="\n".join(type_lines) or "—", inline=False)
    e.add_field(name="By vendor", value="\n".join(vend_lines) or "—", inline=False)
    e.set_footer(text="Owner-only · what the team is buying · month-to-date")
    try: await ch.send(embed=e)
    except Exception as ex: print("lead report send", ex)


# ---- Weekly Quests (results) ----------------------------------------------
def _week_start_pt():
    n = now_pt()
    monday = n - dt.timedelta(days=n.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)

def _iso_week_key():
    return now_pt().strftime("%G-W%V")

def pick_quest():
    return QUESTS[int(now_pt().strftime("%V")) % len(QUESTS)]

def quest_progress(deals):
    by = {}
    for d in deals:
        a = d.get("agent") or "Unknown"
        e = by.setdefault(a, {"ap": 0.0, "apps": 0, "deals": 0, "maxdeal": 0.0})
        ap = _num(d.get("ap"))
        e["ap"] += ap; e["apps"] += int(_num(d.get("apps"))); e["deals"] += 1
        if ap > e["maxdeal"]: e["maxdeal"] = ap
    return by

async def announce_quest(q):
    ch = client.get_channel(QUEST_CH_ID)
    if not ch: return
    e = discord.Embed(title=f"{q['emoji']} Weekly Quest — {q['title']}",
        description=f"{q['desc']}\n\nClear the bar and grab the shoutout. Results only — let's eat. 🍽️",
        color=0x9B59B6)
    e.set_footer(text="Excel Financial · resets Monday · everyone who clears it gets the call-out")
    try: await ch.send(embed=e)
    except Exception as ex: print("quest announce", ex)

async def check_quest():
    """Called after new deals post — shout out anyone who just cleared the active quest."""
    if not SUPABASE_KEY: return
    qs = load_json(QUEST_STATE_FILE, {})
    if qs.get("week") != _iso_week_key(): return       # scheduler hasn't opened this week's quest yet
    q = QUEST_BY_ID.get(qs.get("quest_id"))
    if not q: return
    winners = set(qs.get("winners", []))
    try:
        deals = await fetch_deals_since(_week_start_pt().astimezone(dt.timezone.utc).isoformat())
    except Exception as e:
        print("quest deals", e); return
    prog = quest_progress(deals)
    ch = client.get_channel(QUEST_CH_ID)
    changed = False
    for agent, p in prog.items():
        if str(agent).lower() in IP_EXCLUDE: continue
        if p.get(q["metric"], 0) >= q["goal"] and agent not in winners:
            winners.add(agent); changed = True
            if ch:
                try: await ch.send(f"🎯 **{agent}** just cleared this week's quest — **{q['title']}**! 🔥")
                except Exception as e: print("quest shout", e)
    if changed:
        qs["winners"] = list(winners); save_json(QUEST_STATE_FILE, qs)

async def post_quest_recap():
    qs = load_json(QUEST_STATE_FILE, {})
    q = QUEST_BY_ID.get(qs.get("quest_id"))
    ch = client.get_channel(QUEST_CH_ID)
    if not (q and ch): return
    winners = qs.get("winners", [])
    if winners:
        desc = "Cleared by " + ", ".join(f"**{w}**" for w in winners) + " 👏"
    else:
        desc = "Nobody cleared it this week — new quest drops Monday. Get after it."
    e = discord.Embed(title=f"{q['emoji']} Quest Recap — {q['title']}", description=desc, color=0x9B59B6)
    try: await ch.send(embed=e)
    except Exception as ex: print("quest recap", ex)


# ---- Team Production (manager downline rollups) ---------------------------
def _team_rollup(state, prod):
    """prod = {agent: value}. Credit each producer's value up their full upline chain
       (self included) so every manager gets self + entire downline. -> {name: team_total}."""
    uplines = state.get("uplines") or {}
    team = {}
    for agent, val in (prod or {}).items():
        v = float(val or 0)
        cur, hops, seen = agent, 0, set()
        while cur and hops < 60 and cur not in seen:
            seen.add(cur)
            team[cur] = team.get(cur, 0.0) + v
            cur = uplines.get(cur); hops += 1
    return team

def _downline_counts(state):
    """{manager: number of agents anywhere below them} from the upline tree."""
    uplines = state.get("uplines") or {}
    children = {}
    for a, up in uplines.items():
        if up: children.setdefault(up, []).append(a)
    def desc(m, seen):
        tot = 0
        for c in children.get(m, []):
            if c in seen: continue
            seen.add(c); tot += 1 + desc(c, seen)
        return tot
    return {m: desc(m, set()) for m in children}

def _managers(state):
    """Everyone who is somebody's upline (i.e. actually has a downline)."""
    uplines = state.get("uplines") or {}
    roster = set(state.get("roster") or [])
    return {up for up in uplines.values() if up and (not roster or up in roster)}

def _submitted_ap_by_agent(state, mkey):
    """Per-agent submitted AP for the month, summed from the tracker's OWN deal chips in
       app_state (state.months[mkey].deals). These use canonical/aliased names that match
       the upline tree, so rollups match the tracker exactly — unlike the raw deals table."""
    mdeals = ((state.get("months") or {}).get(mkey) or {}).get("deals") or {}
    out = {}
    for agent, chips in mdeals.items():
        total = sum(_num(d.get("a")) for d in (chips or []))
        if total: out[str(agent)] = total
    return out

def _team_board_embed(state, prod, month_label, *, kind, deal_count=None):
    metric = "IP" if kind == "ip" else "AP"
    team = _team_rollup(state, prod)
    # --- Manager Scoreboard: managers whose DOWNLINE has written business (top 10) ---
    mrows = []
    for mgr in _managers(state):
        if str(mgr).lower() in IP_EXCLUDE: continue    # owner's team = everyone; not a ranking
        own = float(prod.get(mgr, 0) or 0)
        tv = team.get(mgr, 0.0)
        if (tv - own) <= 0: continue                   # no producing downline -> off the manager board
        mrows.append((mgr, tv))
    mrows.sort(key=lambda r: r[1], reverse=True)
    mrows = mrows[:10]
    # --- Individual Scoreboard: top 10 by personal production (owner INCLUDED here) ---
    irows = [(a, float(v or 0)) for a, v in prod.items() if float(v or 0) > 0]
    irows.sort(key=lambda r: r[1], reverse=True)
    irows = irows[:10]
    if not mrows and not irows: return None
    blocks = []
    # --- Excel Financial monthly total across the whole floor ---
    floor_total = sum(float(v or 0) for v in prod.values())
    if kind == "ip":
        blocks.append(f"🏛️ **Excel Financial — {month_label}: ${int(round(floor_total)):,} issued IP**")
    else:
        dtxt = f" · **{deal_count} deals**" if deal_count else ""
        blocks.append(f"🏛️ **Excel Financial — {month_label}: ${int(round(floor_total)):,} AP**{dtxt}")
    if mrows:
        head = f"{'#':<3}{'Manager':<20}{'Team '+metric:>9}"
        L = [head, "─" * len(head)]
        for i, (a, tv) in enumerate(mrows, 1):
            L.append(f"{i:<3}{a[:19]:<20}{_kfmt(tv):>9}")
        blocks.append(f"__**👔 Manager Scoreboard — team {metric}**__\n```\n" + "\n".join(L) + "\n```")
    if irows:
        head = f"{'#':<3}{'Rep':<20}{metric:>9}"
        L = [head, "─" * len(head)]
        for i, (a, v) in enumerate(irows, 1):
            L.append(f"{i:<3}{a[:19]:<20}{_kfmt(v):>9}")
        blocks.append(f"__**🙋 Individual Scoreboard — personal {metric}**__\n```\n" + "\n".join(L) + "\n```")
    if kind == "ip":
        title = f"🏅 Team + Individual IP — {month_label} (issued, month-end)"; color = 0xE67E22
    else:
        title = f"🏆 Team + Individual Production — {month_label}"; color = 0xF1C40F
    e = discord.Embed(title=title, description="\n".join(blocks), color=color)
    e.set_footer(text="Manager = whole team rolled up (needs downline production) · Individual = personal · top 10")
    return e

async def refresh_team_ap_board():
    """Live manager board on SUBMITTED AP — edited in place as deals come in all month."""
    if not SUPABASE_KEY: return
    ch = client.get_channel(TEAM_CH_ID)
    if not ch: return
    state = await fetch_app_state()
    if not state: return
    mkey = _live_month_key(); label = _month_label(mkey)
    prod = _submitted_ap_by_agent(state, mkey)   # canonical names from the tracker -> matches it exactly
    mdeals = ((state.get("months") or {}).get(mkey) or {}).get("deals") or {}
    deal_count = sum(len(chips or []) for chips in mdeals.values())
    e = _team_board_embed(state, prod, label, kind="ap", deal_count=deal_count)
    if not e: return
    async for m in ch.history(limit=25):
        if m.author == client.user and m.embeds and (m.embeds[0].title or "").startswith("🏆 Team"):
            try: await m.edit(embed=e)
            except Exception as ex: print("team edit", ex)
            return
    try: await ch.send(embed=e)
    except Exception as ex: print("team send", ex)

async def post_team_ip_monthly():
    """End-of-month manager board on ISSUED IP (a fresh post, not an edit)."""
    if not SUPABASE_KEY: return
    ch = client.get_channel(TEAM_CH_ID)
    if not ch: return
    state = await fetch_app_state()
    if not state: return
    months = _months_with_ip(state)
    if not months: return
    mkey = months[-1]; label = _month_label(mkey)
    roster = set(str(x).strip().lower() for x in (state.get("roster") or []))
    net = ((state.get("months") or {}).get(mkey) or {}).get("net") or {}
    prod = {}
    for a, v in net.items():
        if roster and str(a).strip().lower() not in roster: continue
        val = _num(v)
        if val > 0: prod[str(a)] = val
    e = _team_board_embed(state, prod, label, kind="ip")
    if not e: return
    try: await ch.send(embed=e)
    except Exception as ex: print("team ip send", ex)


# ---- scheduler ------------------------------------------------------------
_last_daily = None
_last_weekly = None
_last_team_ip = None

@client.event
async def on_ready():
    print(f"Logged in as {client.user}.")
    load_state(); await ensure_start_message()
    if SUPABASE_KEY:
        try: await ensure_lead_roi_message()
        except Exception as e: print("lead roi msg", e)
        try: await refresh_team_ap_board()
        except Exception as e: print("team board", e)
    if not scheduler.is_running(): scheduler.start()
    if SUPABASE_KEY and not wins_poller.is_running(): wins_poller.start()
    if SUPABASE_KEY and not ip_poller.is_running(): ip_poller.start()

@tasks.loop(seconds=30)
async def scheduler():
    global _last_daily, _last_weekly, _last_team_ip
    n = now_pt(); ensure_today()
    # End-of-month manager IP board — once, on the 1st
    if SUPABASE_KEY and n.day == 1 and n.time() >= WEEKLY_TIME and _last_team_ip != (n.year, n.month):
        _last_team_ip = (n.year, n.month); await post_team_ip_monthly()
    if end_today() and n.time() >= end_today() and _last_daily != n.date() and scheduled_start_today() is not None:
        _last_daily = n.date(); await post_daily_report(); await post_deals_daily()
    # New week -> open a fresh results quest (once we're into Monday morning)
    if SUPABASE_KEY:
        qs = load_json(QUEST_STATE_FILE, {})
        if qs.get("week") != _iso_week_key() and n.time() >= dt.time(8, 0):
            q = pick_quest()
            save_json(QUEST_STATE_FILE, {"week": _iso_week_key(), "quest_id": q["id"], "winners": []})
            await announce_quest(q)
    if n.weekday() == WEEKLY_DAY and n.time() >= WEEKLY_TIME and _last_weekly != n.date():
        _last_weekly = n.date()
        await post_weekly()          # recognition + weekly deals
        await post_quest_recap()     # who cleared this week's quest
        await post_lead_roi()        # weekly lead-spend ROI scoreboard (public)
        await post_lead_report()     # lead-buying breakdown by type/vendor (owner-only)
        await refresh_team_ap_board()  # keep the live manager board fresh weekly too
    # IP Reports are import-driven (see ip_poller) — no fixed weekly/monthly schedule.


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set the DISCORD_TOKEN environment variable first (see README).")
    client.run(TOKEN)
