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
  STREAKS & PBs  — results-only, minimum messages: ONE line when a rep hits a milestone
                   run of consecutive closing days (3/5/7/10...), and personal-best weeks
                   listed inside the Sunday Wrap (no extra posts). No quests.
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
import io
import json
import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import tasks

try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False      # cards silently fall back to text if Pillow isn't installed

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
WEEKLY_TIME = dt.time(18, 0)
WEEKLY_DAY  = 6                 # Sunday — the 6 PM PT "Sunday Wrap"
MONTHLY_TIME = dt.time(10, 0)   # 1st-of-month reports post at 10 AM PT

ARRIVAL_PINGS = False   # real-time ON-TIME clock-in lines (off — keeps the log clean)
LATE_PINGS    = True    # real-time LATE arrival notes, as they join (owner + trainers)

# "Out of rooms" tracking: time NOT in a voice room during the scheduled window
# (e.g. 9 AM – 6 PM). Lunch, errands, ghosting — it all counts. Used to spot reps who
# clock in and out on time but don't actually work the day.
AWAY_FLAG_DAILY_MINS  = 60    # flag a day on the daily card when out this many minutes+
AWAY_FLAG_WEEK_HOURS  = 4     # auto-flag repeat offenders at this many hours out per 7 days

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

# --- Rank roles: auto-awarded at the Sunday Wrap (and Top IP on the Final MTD drop) ---
ROLE_CLOSER  = 1531874671220494416   # 💰 Closer of the Week
ROLE_GRINDER = 1531874672176664638   # ⏱️ Grinder of the Week
ROLE_MANAGER = 1531874673237954753   # 👔 Top Manager
ROLE_TOP_IP  = 1531874674215227503   # 📈 Top IP

# --- Slash commands live in #commands (instructions auto-posted there) ---
COMMANDS_CH_ID = 1531874676257591537
TRAINER_ROLE_ID = 1532072525767508290   # 🎓 Trainer — may view attendance data

# --- Durable state: every state file is mirrored to the Supabase bot_state table so
#     history (attendance, streaks, quest winners) SURVIVES redeploys. ---
BOT_STATE_TABLE = "bot_state"

# --- Branded leaderboard cards (black & gold, lion logo). Keep logo.png next to the bot.
LOGO_FILE = "logo.png"
CARD_GOLD  = (212, 175, 55)
CARD_BLACK = (13, 13, 16)
CARD_WHITE = (238, 238, 238)
CARD_DIM   = (150, 150, 150)
CARD_LINE  = (44, 44, 50)
MEDAL_COLS = [(255, 215, 0), (200, 200, 205), (205, 127, 80)]  # gold / silver / bronze

def _card_font(size, bold=False):
    cands = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"] if bold else
             ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans.ttf"])
    for p in cands:
        try: return ImageFont.truetype(p, size)
        except Exception: pass
    try: return ImageFont.load_default(size)
    except Exception: return ImageFont.load_default()

def render_card(title, subtitle, rows, footer="EXCEL FINANCIAL"):
    """rows = [(name, value_str)]. Returns BytesIO PNG, or None if Pillow unavailable."""
    if not HAVE_PIL or not rows: return None
    W = 1000; row_h = 64; top = 208
    H = top + len(rows) * row_h + 84
    img = Image.new("RGB", (W, H), CARD_BLACK)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, W - 11, H - 11], outline=CARD_GOLD, width=3)
    tx = 60
    if os.path.exists(LOGO_FILE):
        try:
            logo = Image.open(LOGO_FILE).convert("RGBA").resize((130, 130), Image.LANCZOS)
            img.paste(logo, (48, 40), logo)
            tx = 210
        except Exception: pass
    d.text((tx, 52), title.upper(), font=_card_font(46, True), fill=CARD_GOLD)
    d.text((tx, 118), subtitle, font=_card_font(27), fill=CARD_WHITE)
    y = top
    for i, (name, val) in enumerate(rows):
        col = MEDAL_COLS[i] if i < 3 else CARD_WHITE
        d.text((64, y), f"{i + 1}", font=_card_font(32, True), fill=col)
        d.text((130, y), str(name)[:26], font=_card_font(32, i < 3), fill=col)
        vf = _card_font(32, True)
        vw = d.textlength(str(val), font=vf)
        d.text((W - 64 - vw, y), str(val), font=vf, fill=CARD_GOLD)
        if i < len(rows) - 1:
            d.line([54, y + row_h - 14, W - 54, y + row_h - 14], fill=CARD_LINE, width=1)
        y += row_h
    d.text((64, H - 58), footer, font=_card_font(20), fill=CARD_DIM)
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
    return buf

async def send_card(ch, embed, title, subtitle, rows, footer="EXCEL FINANCIAL"):
    """Send embed with a branded card image if possible, else the embed alone."""
    buf = render_card(title, subtitle, rows, footer)
    try:
        if buf:
            f = discord.File(buf, filename="card.png")
            embed.set_image(url="attachment://card.png")
            await ch.send(embed=embed, file=f)
        else:
            await ch.send(embed=embed)
    except Exception as ex:
        print("card send", ex)

async def ensure_bot_avatar():
    """One-time: if the bot has no avatar yet, set it to the lion logo automatically."""
    try:
        if client.user and client.user.avatar is None and os.path.exists(LOGO_FILE):
            with open(LOGO_FILE, "rb") as f:
                await client.user.edit(avatar=f.read())
            print("bot avatar set to logo")
    except Exception as e:
        print("avatar", e)

# --- Weekly Quests: rotating RESULTS challenges, auto-tracked from the deals feed ---
# --- Streaks & personal bests: RESULTS-ONLY, minimum messages. -----------------
# A streak = consecutive workdays (Sun ignored) with at least one closed deal.
# The bot posts ONE line only when a rep crosses a milestone run — never daily.
# Personal-best weeks get a line INSIDE the Sunday Wrap (zero extra messages).
STREAK_CH_ID     = RECOGNITION_CH_ID
STREAK_FILE      = "streak_state.json"
PB_FILE          = "pb_state.json"
STREAK_MILESTONES = [3, 5, 7, 10, 15, 20, 30]

# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

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

async def _cloud_push_state(key, d):
    """Mirror one state blob to Supabase so it survives redeploys."""
    if not SUPABASE_KEY: return
    url = f"{SUPABASE_URL}/rest/v1/{BOT_STATE_TABLE}?on_conflict=id"
    headers = {"apikey": SUPABASE_KEY, "authorization": f"Bearer {SUPABASE_KEY}",
               "content-type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    body = {"id": key, "data": d, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status not in (200, 201, 204):
                    print("state push", key, r.status)
    except Exception as e:
        print("state push", key, e)

async def cloud_pull_state():
    """On boot: restore every state file from Supabase (newer cloud copy wins on a fresh box)."""
    if not SUPABASE_KEY: return
    url = f"{SUPABASE_URL}/rest/v1/{BOT_STATE_TABLE}"
    headers = {"apikey": SUPABASE_KEY, "authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params={"select": "id,data"}, headers=headers,
                             timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    print("state pull", r.status); return
                rows = await r.json()
        for row in rows or []:
            key = row.get("id"); data = row.get("data")
            if key and data is not None and not os.path.exists(key):
                with open(key, "w") as f: json.dump(data, f)
        print(f"state restored: {len(rows or [])} blobs")
    except Exception as e:
        print("state pull", e)

def save_json(p, d):
    try:
        with open(p, "w") as f: json.dump(d, f)
    except Exception as e: print("save", p, e)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_cloud_push_state(p, d))   # fire-and-forget cloud mirror
    except RuntimeError:
        pass                                        # no loop yet (startup) — cloud copy comes next save

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

def window_span_seconds():
    """Seconds of the scheduled work window (start -> end) elapsed so far today."""
    start = scheduled_start_today(); end = end_today()
    if start is None or end is None: return 0
    n = now_pt()
    ws = n.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    we = n.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    cur = min(n, we)
    return max(0, int((cur - ws).total_seconds()))

def away_today(rec):
    """Time OUT of the voice rooms during today's work window (lunch and all)."""
    span = window_span_seconds()
    if span <= 0: return 0
    return max(0, span - min(int(live_seconds(rec)), span))

def away_str(sec):
    sec = int(sec)
    return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"


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

def sync_current_voice():
    """On boot: pick up everyone ALREADY sitting in a work room, so a redeploy in the
       middle of a call session never loses live tracking. Anyone the bot thought had
       left (or never saw) gets their clock restarted from now."""
    guild = client.get_guild(GUILD_ID)
    if not guild: return
    ensure_today()
    ts = now_pt(); found = 0
    for vc in guild.voice_channels:
        if not is_work_channel(vc): continue
        for mem in vc.members:
            if mem.bot: continue
            mid = str(mem.id); rec = today.get(mid)
            cam = bool(mem.voice and mem.voice.self_video)
            if rec is None:
                start = scheduled_start_today()
                late = start is not None and ts.time() > start
                today[mid] = {"name": mem.display_name, "first_join": ts.isoformat(),
                              "last_leave": None, "enter_ts": ts.isoformat(), "total_seconds": 0.0,
                              "present": True, "late": late, "camera_on": cam,
                              "cam_ts": ts.isoformat() if cam else None, "camera_seconds": 0.0}
                found += 1
            else:
                if not rec.get("enter_ts"): rec["enter_ts"] = ts.isoformat(); found += 1
                rec["present"] = True; rec["last_leave"] = None
                if cam and not rec.get("camera_on"):
                    rec["camera_on"] = True; rec["cam_ts"] = ts.isoformat()
    if found:
        save_state(); print(f"voice rescan: picked up {found} member(s) already in rooms")

async def announce_arrival(member, ts, late, start):
    """LATE arrivals post to #attendance-log in real time (LATE_PINGS); on-time joins
       stay silent (ARRIVAL_PINGS off) so the log only speaks when something's wrong."""
    if late and LATE_PINGS:
        ch = client.get_channel(LOG_CHANNEL_ID)
        if not ch: return
        mins = int((ts - ts.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)).total_seconds() // 60)
        try:
            await ch.send(f"🔴 **LATE** — **{member.display_name}** joined at **{fmt(ts)}** "
                          f"({mins} min after the {start.strftime('%-I:%M %p')} start).")
        except Exception as e: print("announce", e)
        return
    if not ARRIVAL_PINGS: return
    ch = client.get_channel(LOG_CHANNEL_ID)
    if not ch: return
    try: await ch.send(f"🟢 {member.display_name} clocked in at **{fmt(ts)}** — on time.")
    except Exception as e: print("announce", e)


# ---- history / flags ------------------------------------------------------
def snapshot_today():
    guild = client.get_guild(GUILD_ID)
    start = scheduled_start_today(); day = today_key(); snap = {}
    for mid, rec in today.items():
        snap[mid] = {"name": rec["name"], "seconds": live_seconds(rec),
            "camera_seconds": live_camera(rec), "late": rec["late"],
            "left_early": is_early_leave(rec), "no_show": False,
            "away_seconds": away_today(rec)}
    if guild and start is not None:
        role = guild.get_role(VERIFIED_ROLE_ID)
        if role:
            for m in role.members:
                if not m.bot and str(m.id) not in snap:
                    snap[str(m.id)] = {"name": m.display_name, "seconds": 0,
                        "camera_seconds": 0, "late": False, "left_early": False, "no_show": True,
                        "away_seconds": window_span_seconds()}
    hist = load_json(HISTORY_FILE, {}); hist[day] = snap; save_json(HISTORY_FILE, hist)

def trailing_counts(days=7):
    hist = load_json(HISTORY_FILE, {}); end = now_pt().date(); counts = {}
    today_iso = end.isoformat()
    for i in range(days):
        for mid, r in hist.get((end - dt.timedelta(days=i)).isoformat(), {}).items():
            c = counts.setdefault(mid, {"name": r["name"], "late": 0, "early": 0, "away": 0.0, "hours": 0.0})
            c["name"] = r["name"]
            if r.get("late"): c["late"] += 1
            if r.get("left_early"): c["early"] += 1
            if not r.get("no_show"): c["away"] += r.get("away_seconds", 0) / 3600.0
            c["hours"] += r.get("seconds", 0) / 3600.0
    for mid, rec in today.items():
        if today_iso in hist: break                    # today already snapshotted — don't double-count
        c = counts.setdefault(mid, {"name": rec["name"], "late": 0, "early": 0, "away": 0.0, "hours": 0.0})
        if rec["late"]: c["late"] += 1
        if is_early_leave(rec): c["early"] += 1
        c["away"] += away_today(rec) / 3600.0
        c["hours"] += live_seconds(rec) / 3600.0
    return counts


# ---- daily report (visual card — every person, color-coded) ---------------
DAILY_STATUS_COLORS = {"ontime": (46, 204, 113), "late": (241, 196, 15),
                       "early": (230, 126, 34), "late+early": (231, 76, 60),
                       "noshow": (231, 76, 60), "gaps": (155, 89, 182)}
DAILY_STATUS_LABEL  = {"ontime": "ON TIME", "late": "LATE", "early": "LEFT EARLY",
                       "late+early": "LATE + LEFT EARLY", "noshow": "NO-SHOW",
                       "gaps": "IN-AND-OUT"}

def render_daily_card(day_label, start_label, end_label, rows):
    """rows = [{name, status, detail, hours, cam}] — one row per person, color-coded."""
    if not HAVE_PIL or not rows: return None
    W = 1000; row_h = 58; top = 190
    H = top + len(rows) * row_h + 70
    img = Image.new("RGB", (W, H), CARD_BLACK); d = ImageDraw.Draw(img)
    d.rectangle([10, 10, W - 11, H - 11], outline=CARD_GOLD, width=3)
    tx = 60
    if os.path.exists(LOGO_FILE):
        try:
            logo = Image.open(LOGO_FILE).convert("RGBA").resize((110, 110), Image.LANCZOS)
            img.paste(logo, (46, 36), logo); tx = 190
        except Exception: pass
    d.text((tx, 46), "DAILY ATTENDANCE", font=_card_font(42, True), fill=CARD_GOLD)
    d.text((tx, 104), f"{day_label} · {start_label} – {end_label} PT", font=_card_font(24), fill=CARD_WHITE)
    y = top
    for r in rows:
        col = DAILY_STATUS_COLORS.get(r["status"], CARD_DIM)
        d.rectangle([26, y - 6, 32, y + row_h - 18], fill=col)          # status color bar
        d.ellipse([48, y + 8, 64, y + 24], fill=col)                    # status dot
        d.text((84, y), r["name"][:22], font=_card_font(28, True), fill=CARD_WHITE)
        label = DAILY_STATUS_LABEL.get(r["status"], "")
        if r.get("detail"): label += f"  ·  {r['detail']}"
        d.text((84, y + 30), label, font=_card_font(17), fill=col)
        right = (f"{r['hours']:.1f}h in · cam {r['cam']*100:.0f}%"
                 if r["hours"] > 0 else "—")
        vf = _card_font(22); vw = d.textlength(right, font=vf)
        d.text((W - 56 - vw, y + 6), right, font=vf, fill=CARD_WHITE if r["hours"] > 0 else CARD_DIM)
        d.line([26, y + row_h - 12, W - 26, y + row_h - 12], fill=CARD_LINE, width=1)
        y += row_h
    ok = sum(1 for r in rows if r["status"] == "ontime")
    d.text((56, H - 52), f"{ok}/{len(rows)} clean days · EXCEL FINANCIAL · AFK not counted",
           font=_card_font(19), fill=CARD_DIM)
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
    return buf

def build_daily_rows():
    """Today's per-person rows — GRINDERS ON TOP (most hours in rooms first),
       lightest attendance and no-shows at the bottom."""
    guild = client.get_guild(GUILD_ID)
    rows = []
    for mid, rec in today.items():
        secs = live_seconds(rec); le = is_early_leave(rec); cp = camera_pct(rec)
        away = away_today(rec)
        if rec["late"] and le: status = "late+early"
        elif rec["late"]:      status = "late"
        elif le:               status = "early"
        elif away >= AWAY_FLAG_DAILY_MINS * 60: status = "gaps"   # on time both ends, gone in between
        else:                  status = "ontime"
        bits = []
        if rec["late"] and rec.get("first_join"): bits.append(f"in {fmt(rec['first_join'])}")
        if le and rec.get("last_leave"):          bits.append(f"out {fmt(rec['last_leave'])}")
        if away >= AWAY_FLAG_DAILY_MINS * 60:     bits.append(f"OUT {away_str(away)} of the day")
        if secs >= CAMERA_MIN_MINS*60 and cp < CAMERA_MIN_PCT: bits.append(f"LOW CAM {cp*100:.0f}%")
        rows.append({"name": rec["name"], "status": status, "detail": " · ".join(bits),
                     "hours": secs/3600.0, "cam": cp, "away": away})
    role = guild.get_role(VERIFIED_ROLE_ID) if guild else None
    if role:                                             # every verified member — no-shows included
        for m in role.members:
            if not m.bot and str(m.id) not in today:
                rows.append({"name": m.display_name, "status": "noshow", "detail": "never joined a room",
                             "hours": 0.0, "cam": 0.0, "away": 0.0})
    rows.sort(key=lambda r: (r["status"] == "noshow", -r["hours"]))   # most IN on top, no-shows last
    return rows

async def post_daily_report():
    ch = client.get_channel(LOG_CHANNEL_ID); start = scheduled_start_today()
    if not ch or start is None: return
    end_t = end_today() or dt.time(18, 0)
    rows = build_daily_rows()
    if not rows: return
    day_label = now_pt().strftime("%A, %B %-d")
    buf = render_daily_card(day_label, start.strftime("%-I:%M %p"), end_t.strftime("%-I:%M %p"), rows)
    problems = sum(1 for r in rows if r["status"] != "ontime")
    e = discord.Embed(title=f"📋 Attendance — {day_label}",
        description=(f"**{len(rows) - problems}/{len(rows)}** clean · **{problems}** flagged · "
                     "most hours in rooms on top"),
        color=0xE23B3B if problems else 0x2ECC71)
    e.set_footer(text="Private · Pacific · AFK not counted")
    try:
        if buf:
            f = discord.File(buf, filename="daily.png")
            e.set_image(url="attachment://daily.png")
            await ch.send(embed=e, file=f)
        else:                                            # Pillow missing — text fallback
            for r in rows[:25]:
                e.add_field(name=r["name"],
                            value=f"{DAILY_STATUS_LABEL.get(r['status'],'')} {r.get('detail','')} · {r['hours']:.1f}h",
                            inline=False)
            await ch.send(embed=e)
    except Exception as ex: print("daily", ex)
    snapshot_today()
    await post_autoflags(ch, guild)

async def post_autoflags(ch, guild):
    counts = trailing_counts(7)
    flagged = [c for c in counts.values()
               if c["late"] >= FLAG_LATE or c["early"] >= FLAG_EARLY
               or c.get("away", 0) >= AWAY_FLAG_WEEK_HOURS]
    if not flagged: return
    flagged.sort(key=lambda c: (c["late"] + c["early"], c.get("away", 0)), reverse=True)
    lines = []
    for c in flagged:
        bits = []
        if c["late"]: bits.append(f"**{c['late']}× late**")
        if c["early"]: bits.append(f"**{c['early']}× early leave**")
        if c.get("away", 0) >= AWAY_FLAG_WEEK_HOURS:
            bits.append(f"**{c['away']:.1f}h out of rooms**")
        lines.append(f"• **{c['name']}** — " + " · ".join(bits)
                     + f" — only **{c.get('hours', 0):.1f}h in rooms** this week")
    owner = f"<@{guild.owner_id}> " if guild and guild.owner_id else ""
    e = discord.Embed(title="🚨 AUTO-FLAG — repeat offenders", description="\n".join(lines), color=0xC0392B)
    e.set_footer(text=f"Thresholds: {FLAG_LATE}+ late · {FLAG_EARLY}+ early · {AWAY_FLAG_WEEK_HOURS}h+ out of rooms, per 7 days")
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
                "present": 0, "late": 0, "early": 0, "noshow": 0, "away": 0.0})
            a["name"] = r["name"]; a["hours"] += r.get("seconds", 0)/3600.0
            a["cam"] += r.get("camera_seconds", 0)/3600.0
            if not r.get("no_show"): a["away"] += r.get("away_seconds", 0)/3600.0
            if scheduled:
                a["scheduled"] += 1
                if r.get("no_show"): a["noshow"] += 1
                else:
                    a["present"] += 1
                    if r.get("late"): a["late"] += 1
                    if r.get("left_early"): a["early"] += 1
    return agg

def aggregate_month():
    """Month-to-date attendance per member (same shape as aggregate_week)."""
    hist = load_json(HISTORY_FILE, {}); end = now_pt().date(); agg = {}
    for i in range(end.day):
        day = (end - dt.timedelta(days=i)).isoformat(); snap = hist.get(day)
        if not snap: continue
        scheduled = SCHEDULE.get(dt.date.fromisoformat(day).weekday()) is not None
        for mid, r in snap.items():
            a = agg.setdefault(mid, {"name": r["name"], "hours": 0.0, "cam": 0.0, "scheduled": 0,
                "present": 0, "late": 0, "early": 0, "noshow": 0, "away": 0.0})
            a["name"] = r["name"]; a["hours"] += r.get("seconds", 0)/3600.0
            a["cam"] += r.get("camera_seconds", 0)/3600.0
            if not r.get("no_show"): a["away"] += r.get("away_seconds", 0)/3600.0
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

async def post_sunday_wrap():
    """ONE consolidated public post (Sunday 6 PM PT): recognition + personal bests + card.
       Also awards the weekly rank roles. Private accountability posts separately below."""
    agg = aggregate_week()
    rows = list(agg.values())
    deals = []
    if SUPABASE_KEY:
        start = (now_pt() - dt.timedelta(days=7)).astimezone(dt.timezone.utc).isoformat()
        try: deals = await fetch_deals_since(start)
        except Exception as e: print("recog deals", e)
    ds = summarize_deals(deals)

    rec = client.get_channel(RECOGNITION_CH_ID)
    if rec:
        e = discord.Embed(title="🏁 Sunday Wrap",
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
        # Excel Score — month-to-date top 5 (production + attentiveness in one number)
        try:
            st_data = await fetch_app_state() if SUPABASE_KEY else None
            sc = compute_excel_scores(st_data)
            sc_rows = sorted(((n2, r2["pts"]) for n2, r2 in sc.items()
                              if str(n2).lower() not in IP_EXCLUDE), key=lambda kv: kv[1], reverse=True)[:5]
            if sc_rows:
                e.add_field(name="🏅 Excel Score — month to date",
                    value="\n".join(f"**{i+1}.** {n2} — {p} pts" for i, (n2, p) in enumerate(sc_rows))
                          + "\n*+1/$1k AP · +2 on-time day · −2 late/early · −4 no-show*",
                    inline=False)
        except Exception as ex:
            print("wrap score", ex)
        # personal-best weeks (results-only; zero extra messages — lives inside the wrap)
        pb = load_json(PB_FILE, {})
        new_bests = []
        for a, s in ds["by"].items():
            if str(a).lower() in IP_EXCLUDE: continue
            wk_ap = float(s["ap"])
            prev = float(pb.get(a, 0))
            if prev > 0 and wk_ap > prev:
                new_bests.append((a, wk_ap, prev))
            if wk_ap > prev: pb[a] = wk_ap
        save_json(PB_FILE, pb)
        if new_bests:
            new_bests.sort(key=lambda r: r[1], reverse=True)
            e.add_field(name="🚀 New Personal Bests",
                value="\n".join(f"• **{a}** — ${v:,.2f} (old best ${p:,.2f})" for a, v, p in new_bests[:8]),
                inline=False)
        deal_names = set(str(n).strip().lower() for n in ds["by"])
        warm = [r["name"] for r in rows if r["hours"] >= 5 and r["name"].strip().lower() not in deal_names]
        if warm:
            e.add_field(name="🪑 On the clock, no deals yet",
                value="\n".join("• " + n for n in warm[:10]), inline=False)
        e.set_footer(text="Excel Financial · hours exclude AFK · roles refreshed weekly")
        card_rows = sorted(ds["by"].items(), key=lambda kv: kv[1]["ap"], reverse=True)[:5]
        card_rows = [(nm2, f"${int(s['ap']):,} · {s['deals']}d") for nm2, s in card_rows]
        if card_rows:
            await send_card(rec, e, "Top Closers", now_pt().strftime("Week ending %A, %b %-d"),
                            card_rows, "EXCEL FINANCIAL · SUNDAY WRAP")
        else:
            try: await rec.send(embed=e)
            except Exception as ex: print("recognition", ex)

    # ---- weekly rank roles ----
    try:
        top_closer = max(ds["by"].items(), key=lambda kv: kv[1]["ap"])[0] if ds["by"] else None
        top_hours = max(rows, key=lambda r: r["hours"])["name"] if rows else None
        top_mgr = None
        if SUPABASE_KEY:
            state = await fetch_app_state()
            if state:
                prod = _submitted_ap_by_agent(state, _live_month_key())
                team = _team_rollup(state, prod)
                mgrs = [(mgr, team.get(mgr, 0)) for mgr in _managers(state)
                        if str(mgr).lower() not in IP_EXCLUDE
                        and (team.get(mgr, 0) - float(prod.get(mgr, 0) or 0)) > 0]
                if mgrs: top_mgr = max(mgrs, key=lambda kv: kv[1])[0]
        await award_role(ROLE_CLOSER, top_closer)
        await award_role(ROLE_GRINDER, top_hours)
        await award_role(ROLE_MANAGER, top_mgr)
    except Exception as ex:
        print("rank roles", ex)

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

async def send_onboarding_dm(member):
    """One DM the moment a rep is approved — the house rules, identically every time."""
    e = discord.Embed(title="🦁 Welcome to Excel Financial",
        description=(
            "You're verified — here's how the floor runs. Read this once and you're set.\n\n"
            "**1. Set your server nickname to your REAL NAME** (right-click the server icon → "
            "Edit Server Profile). This is how your production, stats, and weekly awards find you — "
            "wrong nickname, no credit.\n\n"
            "**2. Camera on, on time.** Call sessions run in the voice rooms — the schedule lives in "
            f"<#{1531671450220630166}>. Attendance and camera are tracked automatically.\n\n"
            f"**3. Your results post themselves.** Deals hit <#{WINS_CHANNEL_ID}> live, leaderboards "
            f"update in <#{TEAM_CH_ID}>, and the weekly wrap drops Sundays in <#{RECOGNITION_CH_ID}>.\n\n"
            f"**4. Buy leads? Log every order** with the button in <#{LEAD_ROI_CH_ID}> — vendor, type, "
            "quantity, price. Takes 20 seconds and puts you on the ROI board.\n\n"
            f"**5. Check your own numbers anytime** — type `/mystats` in <#{COMMANDS_CH_ID}>. Only you "
            "see the answer.\n\n"
            "Results get rewarded here — 💰 Closer of the Week, 📈 Top IP, streak call-outs. "
            "Show up, stay on, write business. 🔥"),
        color=0xF1C40F)
    e.set_footer(text="Excel Financial · this is an automated welcome — questions go to the owner")
    try:
        await member.send(embed=e)
    except Exception as e2:
        print("onboarding dm (likely DMs closed)", e2)

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
        # OWNER-ONLY: nobody else can admit or deny people, no matter what they can see.
        if not guild or interaction.user.id != guild.owner_id:
            try:
                await interaction.response.send_message(
                    "⛔ Only the owner can approve or deny access requests.", ephemeral=True)
            except Exception: pass
            return
        uid = int(cid.split("_", 1)[1]); member = guild.get_member(uid) if guild else None
        approver = interaction.user
        if cid.startswith("appr_"):
            role = guild.get_role(VERIFIED_ROLE_ID)
            if member and role:
                try: await member.add_roles(role, reason=f"Approved by {approver}")
                except Exception as e: print("grant", e)
                await send_onboarding_dm(member)
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
    new_agents = []
    for d in order:
        did = str(d.get("id"))
        if did in seen: continue
        if init:
            try:
                await ch.send(fmt_deal(d)); new_posted = True
                if d.get("agent"): new_agents.append(str(d["agent"]))
            except Exception as e: print("wins send", e)
        seen.add(did)
    seen_list = list(seen)[-1000:]
    save_json(WINS_STATE_FILE, {"seen": seen_list, "init": True})
    if new_posted:
        await check_milestones()
        await check_streaks(new_agents)
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
    foot = "Excel Financial · from your Gateway MTD import"
    if fname: foot += f" ({fname})"
    e.set_footer(text=foot)
    if rpt.get("final"):
        card_rows = sorted(nm.items(), key=lambda kv: kv[1], reverse=True)[:n]
        card_title = f"{mname} Final MTD IP"
        card_sub = f"Month-to-date · ${int(round(total)):,} issued"
    else:
        card_rows = sorted(weekly.items(), key=lambda kv: kv[1], reverse=True)[:n]
        card_title = f"{mname} {wtxt} IP Report"
        card_sub = f"Issued since last drop · ${int(round(total)):,}"
    card_rows = [(a, f"${int(round(v)):,}") for a, v in card_rows]
    if card_rows:
        await send_card(ch, e, card_title, card_sub, card_rows, "EXCEL FINANCIAL · ISSUED PREMIUM")
    else:
        e.add_field(name=field, value=board, inline=False)
        try: await ch.send(embed=e)
        except Exception as ex: print("ip report send", ex)
    if rpt.get("final") and nm:                     # 📈 Top IP rank role follows the Final MTD
        try: await award_role(ROLE_TOP_IP, max(nm.items(), key=lambda kv: kv[1])[0])
        except Exception as ex: print("top ip role", ex)

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
    # conversion intel: what a deal/app actually costs, team-wide and per rep
    try:
        mstart = now_pt().replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc).isoformat()
        ds = summarize_deals(await fetch_deals_since(mstart))
        if ds["count"]:
            cpd = total_spend / ds["count"]; cpa = total_spend / max(ds["apps"], 1)
            lpa = total_leads / max(ds["apps"], 1)
            agent_tot = _agent_lead_totals(purchases)
            rep_lines = []
            for a, t in sorted(agent_tot.items(), key=lambda kv: kv[1]["spend"], reverse=True)[:8]:
                aap = ds["by"].get(a, {}).get("apps", 0)
                rep_lines.append(f"• {a} — ${int(t['spend']):,} spend · "
                                 + (f"${t['spend']/aap:,.0f}/app" if aap else "no apps yet"))
            e.add_field(name="📐 Conversion",
                value=(f"Team: **${cpd:,.0f}/deal** · **${cpa:,.0f}/app** · {lpa:.0f} leads per app\n"
                       + "\n".join(rep_lines)), inline=False)
    except Exception as ex:
        print("conversion intel", ex)
    e.set_footer(text="Owner-only · what the team is buying · month-to-date")
    try: await ch.send(embed=e)
    except Exception as ex: print("lead report send", ex)


# ---- Weekly Quests (results) ----------------------------------------------
def _week_start_pt():
    n = now_pt()
    monday = n - dt.timedelta(days=n.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)

async def check_streaks(new_agents):
    """Called with the agents on newly-posted deals. Tracks consecutive closing WORKDAYS
       (Sundays ignored) and posts ONE line only at milestone runs — never daily."""
    if not new_agents: return
    st = load_json(STREAK_FILE, {})
    today = now_pt().date()
    ch = client.get_channel(STREAK_CH_ID)
    changed = False
    for agent in set(new_agents):
        if str(agent).lower() in IP_EXCLUDE: continue
        rec = st.get(agent) or {"last": None, "len": 0, "hit": []}
        if rec["last"] == today.isoformat(): continue          # already counted today
        if rec["last"]:
            try: prev = dt.date.fromisoformat(rec["last"])
            except Exception: prev = None
            missed = 0
            if prev:
                d = prev + dt.timedelta(days=1)
                while d < today:
                    if d.weekday() != 6: missed += 1           # Sundays never break a streak
                    d += dt.timedelta(days=1)
            if prev and missed == 0:
                rec["len"] += 1
            else:
                rec["len"] = 1; rec["hit"] = []
        else:
            rec["len"] = 1; rec["hit"] = []
        rec["last"] = today.isoformat()
        new_ms = [ms for ms in STREAK_MILESTONES if rec["len"] >= ms and ms not in rec["hit"]]
        if new_ms and ch:
            ms = max(new_ms); rec["hit"] = sorted(set(rec["hit"]) | set(new_ms))
            try: await ch.send(f"🔥 **{agent}** — **{rec['len']} straight closing days.** Keep stacking.")
            except Exception as e: print("streak", e)
        st[agent] = rec; changed = True
    if changed: save_json(STREAK_FILE, st)


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

def _apps_by_agent(state, mkey):
    """Per-agent app counts for the month, mirroring the tracker's own math:
       sheet-seeded base count (m.apps) when present, else non-bot chips, plus bot chips."""
    mo = (state.get("months") or {}).get(mkey) or {}
    chips_map = mo.get("deals") or {}
    base_map = mo.get("apps") or {}
    out = {}
    for a in set(chips_map) | set(base_map):
        chips = chips_map.get(a) or []
        bot_chips = sum(1 for d in chips if d.get("bot"))
        base = base_map[a] if a in base_map else sum(1 for d in chips if not d.get("bot"))
        v = int(_num(base)) + bot_chips
        if v: out[str(a)] = v
    return out

def _team_board_embed(state, prod, month_label, *, kind, deal_count=None, apps_map=None):
    metric = "IP" if kind == "ip" else "AP"
    team = _team_rollup(state, prod)
    apps_map = apps_map or {}
    team_apps = _team_rollup(state, apps_map) if apps_map else {}
    # --- Manager Scoreboard: managers whose DOWNLINE has written business (top 10) ---
    mrows = []
    for mgr in _managers(state):
        if str(mgr).lower() in IP_EXCLUDE: continue    # owner's team = everyone; not a ranking
        own = float(prod.get(mgr, 0) or 0)
        tv = team.get(mgr, 0.0)
        if (tv - own) <= 0: continue                   # no producing downline -> off the manager board
        mrows.append((mgr, tv, int(team_apps.get(mgr, 0))))
    mrows.sort(key=lambda r: r[1], reverse=True)
    mrows = mrows[:10]
    # --- Producer Scoreboard: top 10 by personal production (owner INCLUDED here) ---
    irows = [(a, float(v or 0), int(apps_map.get(a, 0))) for a, v in prod.items() if float(v or 0) > 0]
    irows.sort(key=lambda r: r[1], reverse=True)
    irows = irows[:10]
    if not mrows and not irows: return None
    blocks = []
    # --- Excel Financial monthly total across the whole floor ---
    floor_total = sum(float(v or 0) for v in prod.values())
    if kind == "ip":
        blocks.append(f"🏛️ **Excel Financial — {month_label}: ${floor_total:,.2f} issued IP**")
    else:
        dtxt = f" · **{deal_count} deals**" if deal_count else ""
        n = now_pt()
        days_in = (dt.date(n.year + (n.month == 12), (n.month % 12) + 1, 1) - dt.date(n.year, n.month, 1)).days
        proj = floor_total / max(n.day, 1) * days_in
        blocks.append(f"🏛️ **Excel Financial — {month_label}: ${floor_total:,.2f} AP**{dtxt}\n"
                      f"📈 Pace: day {n.day}/{days_in} · projecting **${int(round(proj)):,}** by month end")
    def _pfmt(v): return f"${v:,.2f}"                  # exact, to the penny
    if mrows:
        head = f"{'#':<3}{'Manager':<20}{'Team '+metric:>14}{'Apps':>6}"
        L = [head, "─" * len(head)]
        for i, (a, tv, ap_n) in enumerate(mrows, 1):
            L.append(f"{i:<3}{a[:19]:<20}{_pfmt(tv):>14}{(ap_n if ap_n else '—'):>6}")
        blocks.append(f"__**👔 Manager Scoreboard — team {metric}**__\n```\n" + "\n".join(L) + "\n```")
    if irows:
        head = f"{'#':<3}{'Producer':<20}{metric:>14}{'Apps':>6}"
        L = [head, "─" * len(head)]
        for i, (a, v, ap_n) in enumerate(irows, 1):
            L.append(f"{i:<3}{a[:19]:<20}{_pfmt(v):>14}{(ap_n if ap_n else '—'):>6}")
        blocks.append(f"__**🙋 Producer Scoreboard — personal {metric}**__\n```\n" + "\n".join(L) + "\n```")
    if kind == "ip":
        title = f"🏅 Team IP — {month_label} (issued, month-end)"; color = 0xE67E22
    else:
        title = f"🏆 Team Production — {month_label}"; color = 0xF1C40F
    e = discord.Embed(title=title, description="\n".join(blocks), color=color)
    e.set_footer(text="Manager = whole team rolled up (needs downline production) · Producer = personal · top 10")
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
    e = _team_board_embed(state, prod, label, kind="ap", deal_count=deal_count,
                          apps_map=_apps_by_agent(state, mkey))
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
    net_apps = ((state.get("months") or {}).get(mkey) or {}).get("netApps") or {}
    apps_map = {str(a): int(_num(v)) for a, v in net_apps.items()
                if int(_num(v)) > 0 and (not roster or str(a).strip().lower() in roster)}
    e = _team_board_embed(state, prod, label, kind="ip", apps_map=apps_map)
    if not e: return
    try: await ch.send(embed=e)
    except Exception as ex: print("team ip send", ex)


# ---- Excel Score + Accountability Board ------------------------------------
# EXCEL SCORE (monthly, resets on the 1st): production first, attentiveness enforced.
#   +1 pt per $1,000 submitted AP · +2 per on-time day · −2 per late · −2 per early
#   leave · −4 per no-show. Public: top 5 in the Sunday Wrap + /mystats. Full table
#   lives on the owner's private Accountability Board.
def compute_excel_scores(state):
    att = aggregate_month()
    prod = _submitted_ap_by_agent(state, _live_month_key()) if state else {}
    prod_low = {str(k).strip().lower(): v for k, v in prod.items()}
    used = set()
    rows = {}
    for mid, a in att.items():
        nm = a["name"]; low = nm.strip().lower()
        ap = float(prod_low.get(low, 0.0)); used.add(low)
        ontime = max(a["present"] - a["late"], 0)
        pts = round(ap / 1000) + 2*ontime - 2*a["late"] - 2*a["early"] - 4*a["noshow"]
        rows[nm] = {"pts": pts, "ap": ap,
                    **{k: a.get(k, 0) for k in ("late", "early", "noshow", "present", "hours", "cam", "away")}}
    for nm, ap in prod.items():                       # producers with no attendance record yet
        if str(nm).strip().lower() in used: continue
        rows[nm] = {"pts": round(float(ap)/1000), "ap": float(ap),
                    "late": 0, "early": 0, "noshow": 0, "present": 0, "hours": 0.0, "cam": 0.0, "away": 0.0}
    return rows

async def refresh_accountability_board():
    """Owner-only bird's-eye: ONE live message in #attendance-log, edited in place daily.
       Per rep: lates / earlies / no-shows / hours / camera% NEXT TO their AP and Excel
       Score — so weak results and weak attentiveness sit on the same line."""
    ch = client.get_channel(LOG_CHANNEL_ID)
    if not ch: return
    state = await fetch_app_state() if SUPABASE_KEY else None
    rows = compute_excel_scores(state)
    if not rows: return
    order = sorted(rows.items(), key=lambda kv: (kv[1]["late"] + kv[1]["early"] + kv[1]["noshow"],
                                                 kv[1].get("away", 0), -kv[1]["ap"]), reverse=True)
    head = f"{'Rep':<13}{'L':>3}{'E':>3}{'NS':>3}{'Hrs':>6}{'Out':>6}{'Cam':>5}{'AP':>12}{'Sc':>4}"
    L = [head, "─" * len(head)]
    for nm, r in order[:25]:
        campct = (r["cam"] / r["hours"] * 100) if r["hours"] else 0
        L.append(f"{nm[:12]:<13}{r['late']:>3}{r['early']:>3}{r['noshow']:>3}"
                 f"{r['hours']:>6.1f}{r.get('away', 0):>6.1f}{campct:>4.0f}%"
                 f"{'$'+format(r['ap'], ',.2f'):>12}{r['pts']:>4}")
    e = discord.Embed(title=f"📋 Accountability Board — {_month_label(_live_month_key())}",
        description=("Worst attendance first · results on the same line\n```\n" + "\n".join(L) + "\n```"),
        color=0xE23B3B)
    # nickname audit — anyone the data can't match is a silent hole in the stats
    if state:
        guild = client.get_guild(GUILD_ID)
        vrole = guild.get_role(VERIFIED_ROLE_ID) if guild else None
        if vrole:
            bad = [mm.display_name for mm in vrole.members
                   if not mm.bot and mm.id != guild.owner_id
                   and not match_roster(state, mm.display_name)]
            if bad:
                e.add_field(name=f"⚠️ Nickname ≠ roster ({len(bad)}) — their stats can't link up",
                    value="\n".join("• " + n for n in bad[:12]) + ("\n…" if len(bad) > 12 else ""),
                    inline=False)
    e.set_footer(text="Owner + trainers · L=late E=early NS=no-show · Out=hrs out of rooms 9–6 · Sc=Excel Score · daily")
    async for msg in ch.history(limit=40):
        if msg.author == client.user and msg.embeds and (msg.embeds[0].title or "").startswith("📋 Accountability Board"):
            try: await msg.edit(embed=e)
            except Exception as ex: print("acct edit", ex)
            return
    try: await ch.send(embed=e)
    except Exception as ex: print("acct send", ex)


# ---- Weekly Attendance Matrix (owner + trainers, #attendance-log) ----------
def _blend(c, a=0.42, bg=CARD_BLACK):
    return tuple(int(c[i] * a + bg[i] * (1 - a)) for i in range(3))

def render_week_matrix(week_label, days, people):
    """people = [{name, cells:[{status,hours}...] per day, tot_h, tot_out, cam, l, e, ns}]
       One row per person, one colored cell per day, totals on the right."""
    if not HAVE_PIL or not people: return None
    NAME_W = 200; CELL_W = 92; TOT_W = 345; ROW_H = 46; TOP = 168
    W = 40 + NAME_W + CELL_W * len(days) + TOT_W + 40
    H = TOP + ROW_H * len(people) + 118
    img = Image.new("RGB", (W, H), CARD_BLACK); d = ImageDraw.Draw(img)
    d.rectangle([10, 10, W - 11, H - 11], outline=CARD_GOLD, width=3)
    tx = 44
    if os.path.exists(LOGO_FILE):
        try:
            logo = Image.open(LOGO_FILE).convert("RGBA").resize((92, 92), Image.LANCZOS)
            img.paste(logo, (40, 30), logo); tx = 152
        except Exception: pass
    d.text((tx, 40), "WEEKLY ATTENDANCE", font=_card_font(36, True), fill=CARD_GOLD)
    d.text((tx, 90), week_label, font=_card_font(21), fill=CARD_WHITE)
    # column headers
    hy = TOP - 34
    for i, day in enumerate(days):
        x = 40 + NAME_W + i * CELL_W
        lbl = day.strftime("%a").upper()
        f = _card_font(18, True); lw = d.textlength(lbl, font=f)
        d.text((x + (CELL_W - lw) / 2, hy), lbl, font=f, fill=CARD_DIM)
    d.text((40 + NAME_W + len(days) * CELL_W + 16, hy), "WEEK TOTALS", font=_card_font(18, True), fill=CARD_DIM)
    y = TOP
    for p in people:
        d.text((44, y + 10), p["name"][:16], font=_card_font(22, True), fill=CARD_WHITE)
        for i, c in enumerate(p["cells"]):
            x = 40 + NAME_W + i * CELL_W
            col = DAILY_STATUS_COLORS.get(c["status"])
            if c["status"] == "none":
                d.text((x + CELL_W/2 - 6, y + 10), "—", font=_card_font(20), fill=(70, 70, 76))
            else:
                d.rounded_rectangle([x + 4, y + 3, x + CELL_W - 8, y + ROW_H - 9], radius=7, fill=_blend(col))
                txt = f"{c['hours']:.1f}"
                f = _card_font(19, True); tw = d.textlength(txt, font=f)
                d.text((x + (CELL_W - 4 - tw) / 2, y + 11), txt, font=f, fill=(235, 235, 235))
        tot = (f"{p['tot_h']:.1f}h · out {p['tot_out']:.1f}h · cam {p['cam']*100:.0f}%"
               + (f" · {p['l']}L" if p['l'] else "") + (f" {p['e']}E" if p['e'] else "")
               + (f" {p['ns']}NS" if p['ns'] else ""))
        d.text((40 + NAME_W + len(days) * CELL_W + 16, y + 12), tot, font=_card_font(18), fill=CARD_WHITE)
        d.line([36, y + ROW_H - 4, W - 36, y + ROW_H - 4], fill=CARD_LINE, width=1)
        y += ROW_H
    # legend
    ly = H - 84
    lx = 44
    for status, lbl in (("ontime", "ON TIME"), ("late", "LATE"), ("early", "LEFT EARLY"),
                        ("late+early", "BOTH"), ("noshow", "NO-SHOW"), ("gaps", "IN-AND-OUT")):
        col = DAILY_STATUS_COLORS[status]
        d.rounded_rectangle([lx, ly, lx + 26, ly + 18], radius=5, fill=_blend(col))
        f = _card_font(16); d.text((lx + 34, ly), lbl, font=f, fill=CARD_DIM)
        lx += 34 + d.textlength(lbl, font=f) + 26
    d.text((44, H - 48), "cell number = hours in rooms that day · owner + trainers only · EXCEL FINANCIAL",
           font=_card_font(16), fill=CARD_DIM)
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
    return buf

def build_week_people():
    """This week's per-person, per-day grid data — GRINDERS ON TOP (total hours in)."""
    n = now_pt().date()
    monday = n - dt.timedelta(days=n.weekday())
    days = [monday + dt.timedelta(days=i) for i in range(6)]   # Mon..Sat
    hist = load_json(HISTORY_FILE, {})
    people_map = {}
    for i, day in enumerate(days):
        snap = hist.get(day.isoformat()) or {}
        for mid, r in snap.items():
            p = people_map.setdefault(mid, {"name": r["name"],
                "cells": [{"status": "none", "hours": 0.0} for _ in days],
                "tot_h": 0.0, "tot_out": 0.0, "cam_h": 0.0, "l": 0, "e": 0, "ns": 0})
            p["name"] = r["name"]
            hrs = r.get("seconds", 0) / 3600.0
            away = r.get("away_seconds", 0) / 3600.0
            if r.get("no_show"): status = "noshow"; p["ns"] += 1
            elif r.get("late") and r.get("left_early"): status = "late+early"; p["l"] += 1; p["e"] += 1
            elif r.get("late"): status = "late"; p["l"] += 1
            elif r.get("left_early"): status = "early"; p["e"] += 1
            elif away >= AWAY_FLAG_DAILY_MINS / 60.0: status = "gaps"   # away is in hours here
            else: status = "ontime"
            p["cells"][i] = {"status": status, "hours": hrs}
            p["tot_h"] += hrs; p["cam_h"] += r.get("camera_seconds", 0) / 3600.0
            if not r.get("no_show"): p["tot_out"] += away
    people = []
    for p in people_map.values():
        p["cam"] = (p["cam_h"] / p["tot_h"]) if p["tot_h"] else 0.0
        people.append(p)
    people.sort(key=lambda p: -p["tot_h"])               # most hours in rooms on top
    label = f"{days[0].strftime('%b %-d')} – {days[-1].strftime('%b %-d, %Y')} · Mon–Sat"
    return label, days, people

async def post_weekly_attendance():
    """Sunday: the detailed week grid — every person, every day, hours + status."""
    ch = client.get_channel(LOG_CHANNEL_ID)
    if not ch: return
    label, days, people = build_week_people()
    if not people: return
    buf = render_week_matrix(label, days, people)
    e = discord.Embed(title=f"🗓️ Weekly Attendance Detail — week of {days[0].strftime('%b %-d')}",
        description=f"**{len(people)}** tracked · most hours in rooms on top",
        color=0x3498DB)
    e.set_footer(text="Owner + trainers · full per-day breakdown")
    try:
        if buf:
            f = discord.File(buf, filename="week.png")
            e.set_image(url="attachment://week.png")
            await ch.send(embed=e, file=f)
        else:
            await ch.send(embed=e)
    except Exception as ex: print("week matrix", ex)


# ---- rank roles ------------------------------------------------------------
def find_member(name):
    """Best-effort roster-name -> guild member (display name / global name match)."""
    guild = client.get_guild(GUILD_ID)
    if not (guild and name): return None
    q = str(name).strip().lower()
    for m in guild.members:
        for cand in (m.display_name, getattr(m, "global_name", None), m.name):
            if cand and cand.strip().lower() == q: return m
    first = q.split()[0]
    hits = [m for m in guild.members if m.display_name.strip().lower().startswith(first)]
    return hits[0] if len(hits) == 1 else None

async def award_role(role_id, winner_name):
    """Move a weekly rank role to its new holder (clears previous holders)."""
    guild = client.get_guild(GUILD_ID)
    role = guild.get_role(role_id) if guild else None
    if not role: return
    winner = find_member(winner_name) if winner_name else None
    for m in list(role.members):
        if m != winner:
            try: await m.remove_roles(role, reason="weekly rank rotation")
            except Exception as e: print("role rm", e)
    if winner and role not in winner.roles:
        try: await winner.add_roles(role, reason="weekly rank award")
        except Exception as e: print("role add", e)


# ---- slash commands (#commands) --------------------------------------------
async def ensure_commands_message():
    ch = client.get_channel(COMMANDS_CH_ID)
    if not ch: return
    e = discord.Embed(title="🤖 Excel Bot — Commands",
        description=(
            "Type `/` in any channel and pick a command. **Answers are private — only you see them.**\n\n"
            "**/mystats** — your week: hours, camera %, lates · your month: deals, AP, lead spend & ROI\n"
            "**/leaderboard** — this month's top 10 by personal AP + the team total\n"
            "**/team** — this month's top managers by team AP (full downlines)\n"
            "**/pace** — where the month is tracking vs. where it'll land\n"
            "**/attendance** — *(owner & trainers only)* today / week / month attendance views\n\n"
            "**Logging lead orders** → hit the button in <#" + str(LEAD_ROI_CH_ID) + "> every time you buy.\n"
            "*Tip: keep your server nickname set to your real name so the bot can match your production.*"),
        color=0x5865F2)
    e.set_footer(text="Excel Financial · commands are free to spam — nobody else sees them")
    async for m in ch.history(limit=10):
        if m.author == client.user and m.embeds:
            try: await m.edit(embed=e)
            except Exception: pass
            return
    try:
        msg = await ch.send(embed=e)
        try: await msg.pin()
        except Exception: pass
    except Exception as ex: print("commands msg", ex)

def _fmt_x(ap, spend): return f"{ap/spend:.1f}x" if spend else "—"

@tree.command(name="mystats", description="Your private stats — week attendance + month production", guild=discord.Object(GUILD_ID))
async def cmd_mystats(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    name = interaction.user.display_name
    lines = []
    row = next((r for r in aggregate_week().values()
                if r["name"].strip().lower() == name.strip().lower()), None)
    if row:
        campct = (row["cam"] / row["hours"] * 100) if row["hours"] else 0
        lines.append(f"**This week:** {row['hours']:.1f}h on the floor · out {row.get('away', 0):.1f}h · "
                     f"camera {campct:.0f}% · {row['late']} late · {row['early']} early leave")
    else:
        lines.append("**This week:** no floor time logged yet")
    if SUPABASE_KEY:
        state = await fetch_app_state()
        canon = match_roster(state, name) if state else None
        if canon:
            mkey = _live_month_key()
            prod = _submitted_ap_by_agent(state, mkey)
            chips = ((state.get("months") or {}).get(mkey) or {}).get("deals") or {}
            my_ap = prod.get(canon, 0); my_deals = len(chips.get(canon) or [])
            lines.append(f"**{_month_label(mkey)}:** {my_deals} deals · **${int(round(my_ap)):,} AP**")
            ipv = _net_map(state, mkey).get(canon)
            if ipv: lines.append(f"**Issued IP:** ${int(round(ipv)):,}")
            t = _agent_lead_totals(await fetch_lead_purchases(mkey)).get(canon)
            if t:
                lines.append(f"**Lead spend:** ${int(round(t['spend'])):,} · {t['leads']} leads · "
                             f"AP return {_fmt_x(my_ap, t['spend'])}")
        else:
            lines.append("_Couldn't match your nickname to the roster — set your server nickname to your real name._")
        try:
            sc = compute_excel_scores(state)
            ranked = sorted(((n2, r2["pts"]) for n2, r2 in sc.items()
                             if str(n2).lower() not in IP_EXCLUDE), key=lambda kv: kv[1], reverse=True)
            mine = next(((i + 1, p) for i, (n2, p) in enumerate(ranked)
                         if n2.strip().lower() == name.strip().lower()
                         or (canon and n2.strip().lower() == canon.strip().lower())), None)
            if mine:
                lines.append(f"**Excel Score:** {mine[1]} pts · #{mine[0]} of {len(ranked)}")
        except Exception as ex:
            print("mystats score", ex)
    e = discord.Embed(title=f"📊 {name}", description="\n".join(lines), color=0x1ABC9C)
    await interaction.followup.send(embed=e, ephemeral=True)

@tree.command(name="leaderboard", description="This month's top 10 by personal AP (private view)", guild=discord.Object(GUILD_ID))
async def cmd_leaderboard(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not SUPABASE_KEY: return await interaction.followup.send("Data source offline.", ephemeral=True)
    state = await fetch_app_state()
    if not state: return await interaction.followup.send("Couldn't reach the tracker.", ephemeral=True)
    mkey = _live_month_key(); prod = _submitted_ap_by_agent(state, mkey)
    apps = _apps_by_agent(state, mkey)
    rows = sorted(((a, v) for a, v in prod.items() if v > 0), key=lambda r: r[1], reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉"]
    body = "\n".join(f"{medals[i] if i < 3 else f'**{i+1}.**'} {a} — **${v:,.2f}**"
                     + (f" · {apps[a]} apps" if apps.get(a) else "")
                     for i, (a, v) in enumerate(rows)) or "No production yet."
    total = sum(prod.values())
    e = discord.Embed(title=f"🏆 Producer Scoreboard — {_month_label(mkey)}",
        description=body + f"\n\n🏛️ Team total: **${total:,.2f}**", color=0xF1C40F)
    await interaction.followup.send(embed=e, ephemeral=True)

@tree.command(name="team", description="This month's top managers by team AP (private view)", guild=discord.Object(GUILD_ID))
async def cmd_team(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not SUPABASE_KEY: return await interaction.followup.send("Data source offline.", ephemeral=True)
    state = await fetch_app_state()
    if not state: return await interaction.followup.send("Couldn't reach the tracker.", ephemeral=True)
    mkey = _live_month_key(); prod = _submitted_ap_by_agent(state, mkey)
    team = _team_rollup(state, prod)
    rows = [(mgr, team.get(mgr, 0)) for mgr in _managers(state)
            if str(mgr).lower() not in IP_EXCLUDE
            and (team.get(mgr, 0) - float(prod.get(mgr, 0) or 0)) > 0]
    rows.sort(key=lambda r: r[1], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    body = "\n".join(f"{medals[i] if i < 3 else f'**{i+1}.**'} {a} — **${v:,.2f}**"
                     for i, (a, v) in enumerate(rows[:10])) or "No team production yet."
    e = discord.Embed(title=f"👔 Manager Scoreboard — {_month_label(mkey)}", description=body, color=0x9B59B6)
    await interaction.followup.send(embed=e, ephemeral=True)

@tree.command(name="pace", description="Where the month is tracking (private view)", guild=discord.Object(GUILD_ID))
async def cmd_pace(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not SUPABASE_KEY: return await interaction.followup.send("Data source offline.", ephemeral=True)
    state = await fetch_app_state()
    if not state: return await interaction.followup.send("Couldn't reach the tracker.", ephemeral=True)
    n = now_pt(); mkey = _live_month_key()
    prod = _submitted_ap_by_agent(state, mkey); total = sum(prod.values())
    days_in = (dt.date(n.year + (n.month == 12), (n.month % 12) + 1, 1) - dt.date(n.year, n.month, 1)).days
    proj = total / max(n.day, 1) * days_in
    wk_start = (n - dt.timedelta(days=n.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    try: wdeals = summarize_deals(await fetch_deals_since(wk_start.astimezone(dt.timezone.utc).isoformat()))
    except Exception: wdeals = {"apps": 0}
    pct = (wdeals["apps"] / WEEKLY_APPS_GOAL) if WEEKLY_APPS_GOAL else 0
    e = discord.Embed(title=f"📈 Pace — {_month_label(mkey)}",
        description=(f"**${int(round(total)):,} AP** through day {n.day} of {days_in}\n"
                     f"Projected month end: **${int(round(proj)):,}**\n\n"
                     f"This week: **{wdeals['apps']} / {WEEKLY_APPS_GOAL} apps**\n`{bar(pct)}` {pct*100:.0f}%"),
        color=0x3498DB)
    await interaction.followup.send(embed=e, ephemeral=True)


# ---- Hours vs Production quadrant (owner + trainers) -----------------------
QUAD_COLORS = {"core": (46, 204, 113), "coach": (241, 196, 15),
               "wild": (230, 126, 34), "exit": (231, 76, 60)}
QUAD_KEY = [
    ("core",  "THE CORE",      "high hours + high AP — feed them, clone them"),
    ("coach", "COACHABLE",     "high hours, low AP — effort's there, fix the skill"),
    ("wild",  "WILDCARDS",     "low hours, high AP — talent, culture risk"),
    ("exit",  "DECISION TIME", "low hours, low AP — not showing, not writing"),
]

def build_quadrant_points(state):
    """{name: {h: month hours in rooms, ap: month submitted AP}} — owner excluded."""
    att = aggregate_month()
    prod = _submitted_ap_by_agent(state, _live_month_key()) if state else {}
    points = {}
    for a in att.values():
        nm = a["name"]; canon = (match_roster(state, nm) if state else None) or nm
        if str(canon).lower() in IP_EXCLUDE: continue
        p = points.setdefault(canon, {"h": 0.0, "ap": 0.0})
        p["h"] += a["hours"]
        if canon in prod: p["ap"] = float(prod[canon])
    for nm, ap in prod.items():
        if nm in points or str(nm).lower() in IP_EXCLUDE: continue
        points[nm] = {"h": 0.0, "ap": float(ap)}          # writes but never in rooms
    return points

def _median(vals):
    s = sorted(vals); n = len(s)
    return 0 if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)

def render_quadrant(points, month_label):
    """Scatter of every rep: X = hours in rooms, Y = AP written. Median cross-hairs
       split the floor into the four kinds of people; the key is printed on the card."""
    if not HAVE_PIL or not points: return None
    W, H = 1150, 940
    PLOT = (110, 170, W - 60, H - 250)                    # x0,y0,x1,y1
    img = Image.new("RGB", (W, H), CARD_BLACK); d = ImageDraw.Draw(img)
    d.rectangle([10, 10, W - 11, H - 11], outline=CARD_GOLD, width=3)
    tx = 44
    if os.path.exists(LOGO_FILE):
        try:
            logo = Image.open(LOGO_FILE).convert("RGBA").resize((96, 96), Image.LANCZOS)
            img.paste(logo, (40, 32), logo); tx = 156
        except Exception: pass
    d.text((tx, 42), "HOURS vs PRODUCTION", font=_card_font(38, True), fill=CARD_GOLD)
    d.text((tx, 94), f"{month_label} · every rep · hours in rooms → AP written", font=_card_font(21), fill=CARD_WHITE)
    hs = [p["h"] for p in points.values()]; aps = [p["ap"] for p in points.values()]
    max_h = max(max(hs) * 1.12, 1.0); max_ap = max(max(aps) * 1.12, 1.0)
    med_h = _median(hs); med_ap = _median(aps)
    x0, y0, x1, y1 = PLOT
    def X(h): return x0 + (h / max_h) * (x1 - x0)
    def Y(ap): return y1 - (ap / max_ap) * (y1 - y0)
    mx, my = X(med_h), Y(med_ap)
    # quadrant tints
    for rect, key in (((mx, y0, x1, my), "core"), ((x0, y0, mx, my), "wild"),
                      ((mx, my, x1, y1), "coach"), ((x0, my, mx, y1), "exit")):
        d.rectangle(rect, fill=_blend(QUAD_COLORS[key], 0.13))
    # median cross-hairs (dashed)
    for yy in range(int(y0), int(y1), 14): d.line([mx, yy, mx, min(yy + 7, y1)], fill=CARD_GOLD, width=2)
    for xx in range(int(x0), int(x1), 14): d.line([xx, my, min(xx + 7, x1), my], fill=CARD_GOLD, width=2)
    d.rectangle(PLOT, outline=(70, 70, 78), width=2)
    # axis labels
    d.text((x0, y1 + 12), "0h", font=_card_font(17), fill=CARD_DIM)
    d.text((x1 - 60, y1 + 12), f"{max_h:.0f}h in", font=_card_font(17), fill=CARD_DIM)
    d.text((28, y0 - 4), f"${max_ap/1000:.0f}k", font=_card_font(16), fill=CARD_DIM)
    d.text((28, y1 - 16), "$0", font=_card_font(16), fill=CARD_DIM)
    d.text((mx + 6, y1 + 12), f"median {med_h:.0f}h", font=_card_font(16), fill=CARD_GOLD)
    d.text((x0 + 6, my - 22), f"median ${med_ap/1000:.1f}k", font=_card_font(16), fill=CARD_GOLD)
    # dots + names, colored by quadrant
    lf = _card_font(16)
    for nm, p in sorted(points.items(), key=lambda kv: -kv[1]["ap"]):
        hx, hy = X(p["h"]), Y(p["ap"])
        key = ("core" if p["h"] >= med_h and p["ap"] >= med_ap else
               "wild" if p["ap"] >= med_ap else
               "coach" if p["h"] >= med_h else "exit")
        col = QUAD_COLORS[key]
        d.ellipse([hx - 7, hy - 7, hx + 7, hy + 7], fill=col, outline=CARD_BLACK)
        label = nm.split()[0][:10]
        lx = hx + 11 if hx < x1 - 110 else hx - 11 - d.textlength(label, font=lf)
        ly = max(y0 + 2, min(hy - 8, y1 - 18))
        d.text((lx, ly), label, font=lf, fill=CARD_WHITE)
    # THE KEY — the four kinds of people
    ky = y1 + 42
    d.text((44, ky), "THE FOUR KINDS OF PEOPLE", font=_card_font(21, True), fill=CARD_GOLD)
    ky += 34
    for key, name, desc in QUAD_KEY:
        col = QUAD_COLORS[key]
        d.ellipse([44, ky + 3, 60, ky + 19], fill=col)
        nf = _card_font(19, True)
        d.text((70, ky), name, font=nf, fill=col)
        d.text((70 + d.textlength(name, font=nf) + 14, ky + 2), "— " + desc,
               font=_card_font(17), fill=CARD_WHITE)
        ky += 33
    d.text((44, H - 46), "dot = one rep · cross-hairs = team medians · owner + trainers only · EXCEL FINANCIAL",
           font=_card_font(15), fill=CARD_DIM)
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
    return buf

async def post_quadrant():
    """Sunday: the hours-vs-production quadrant to #attendance-log (owner + trainers)."""
    ch = client.get_channel(LOG_CHANNEL_ID)
    if not ch: return
    state = await fetch_app_state() if SUPABASE_KEY else None
    points = build_quadrant_points(state)
    if len(points) < 2: return
    buf = render_quadrant(points, _month_label(_live_month_key()))
    if not buf: return
    try: await ch.send(file=discord.File(buf, filename="quadrant.png"))
    except Exception as ex: print("quadrant", ex)

def _can_view_attendance(user):
    guild = client.get_guild(GUILD_ID)
    if not guild: return False
    if user.id == guild.owner_id: return True
    member = guild.get_member(user.id)
    return bool(member and any(r.id == TRAINER_ROLE_ID for r in member.roles))

@tree.command(name="attendance", description="Owner/trainers: attendance — today, this week, or this month",
              guild=discord.Object(GUILD_ID))
@discord.app_commands.describe(period="Which view do you want?")
@discord.app_commands.choices(period=[
    discord.app_commands.Choice(name="today", value="today"),
    discord.app_commands.Choice(name="this week", value="week"),
    discord.app_commands.Choice(name="this month", value="month"),
    discord.app_commands.Choice(name="hours vs production", value="quad")])
async def cmd_attendance(interaction: discord.Interaction, period: discord.app_commands.Choice[str]):
    if not _can_view_attendance(interaction.user):
        return await interaction.response.send_message("⛔ Owner and trainers only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    p = period.value
    try:
        if p == "today":
            ensure_today()
            rows = build_daily_rows()
            if not rows: return await interaction.followup.send("No attendance data yet today.", ephemeral=True)
            start = scheduled_start_today() or dt.time(9, 0); end_t = end_today() or dt.time(18, 0)
            buf = render_daily_card(now_pt().strftime("%A, %B %-d"),
                                    start.strftime("%-I:%M %p"), end_t.strftime("%-I:%M %p"), rows)
            if buf: return await interaction.followup.send(file=discord.File(buf, "today.png"), ephemeral=True)
        elif p == "week":
            label, days, people = build_week_people()
            if not people: return await interaction.followup.send("No attendance data for this week yet.", ephemeral=True)
            buf = render_week_matrix(label, days, people)
            if buf: return await interaction.followup.send(file=discord.File(buf, "week.png"), ephemeral=True)
        elif p == "quad":
            state = await fetch_app_state() if SUPABASE_KEY else None
            points = build_quadrant_points(state)
            if len(points) < 2: return await interaction.followup.send("Not enough data yet for the quadrant.", ephemeral=True)
            buf = render_quadrant(points, _month_label(_live_month_key()))
            if buf: return await interaction.followup.send(file=discord.File(buf, "quadrant.png"), ephemeral=True)
        else:  # month
            agg = aggregate_month()
            if not agg: return await interaction.followup.send("No attendance data this month yet.", ephemeral=True)
            rows = sorted(agg.values(), key=lambda a: -a["hours"])   # grinders on top
            card_rows = [(a["name"], f"{a['hours']:.1f}h · {a['late']}L {a['early']}E {a['noshow']}NS")
                         for a in rows[:20]]
            total_h = sum(a["hours"] for a in rows)
            buf = render_card("Monthly Attendance", f"{_month_label(_live_month_key())} · "
                              f"{total_h:.0f} team hours · most in on top",
                              card_rows, "EXCEL FINANCIAL · HOURS IN ROOMS · L=LATE E=EARLY NS=NO-SHOW")
            if buf: return await interaction.followup.send(file=discord.File(buf, "month.png"), ephemeral=True)
        await interaction.followup.send("Couldn't render that view.", ephemeral=True)
    except Exception as ex:
        print("attendance cmd", ex)
        try: await interaction.followup.send("Something went wrong building that view.", ephemeral=True)
        except Exception: pass


# ---- monthly analytics (1st of month) --------------------------------------
def render_trend_chart(labels, ap_vals, ip_vals):
    """Simple branded AP-vs-IP bar chart. Returns BytesIO PNG or None."""
    if not HAVE_PIL or not labels: return None
    W, H = 1000, 520; PAD = 70
    img = Image.new("RGB", (W, H), CARD_BLACK); d = ImageDraw.Draw(img)
    d.rectangle([10, 10, W - 11, H - 11], outline=CARD_GOLD, width=3)
    d.text((40, 28), "TEAM PRODUCTION TREND", font=_card_font(34, True), fill=CARD_GOLD)
    d.text((40, 74), "Submitted AP (gold) vs Issued IP (white) by month", font=_card_font(20), fill=CARD_DIM)
    peak = max(ap_vals + ip_vals + [1])
    plot_top, plot_bot = 130, H - 80
    n = len(labels); slot = (W - 2 * PAD) / max(n, 1)
    for i in range(n):
        cx = PAD + slot * i + slot / 2
        for off, val, col in ((-22, ap_vals[i], CARD_GOLD), (4, ip_vals[i], CARD_WHITE)):
            bh = int((plot_bot - plot_top) * (val / peak))
            d.rectangle([cx + off, plot_bot - bh, cx + off + 18, plot_bot], fill=col)
        f = _card_font(18); lw = d.textlength(labels[i], font=f)
        d.text((cx - lw / 2, plot_bot + 12), labels[i], font=f, fill=CARD_WHITE)
    d.text((40, H - 42), f"peak ${peak/1000:.0f}k · EXCEL FINANCIAL", font=_card_font(16), fill=CARD_DIM)
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
    return buf

async def post_trend_chart():
    if not SUPABASE_KEY: return
    ch = client.get_channel(TEAM_CH_ID)
    if not ch: return
    state = await fetch_app_state()
    if not state: return
    months = sorted((state.get("months") or {}).keys())[-6:]
    labels, apv, ipv = [], [], []
    for mk in months:
        prod = _submitted_ap_by_agent(state, mk)
        labels.append(_month_label(mk).split()[0][:3].upper())
        apv.append(sum(prod.values()))
        ipv.append(sum(_net_map(state, mk).values()))
    if not any(apv) and not any(ipv): return
    buf = render_trend_chart(labels, apv, ipv)
    if not buf: return
    try: await ch.send(file=discord.File(buf, filename="trend.png"))
    except Exception as ex: print("trend", ex)

async def post_ntg_report():
    """Owner-only: whose business actually issues. NTG = issued IP / submitted AP,
       for the most recent complete month that has Gateway data."""
    if not SUPABASE_KEY: return
    ch = client.get_channel(LEAD_REPORT_CH_ID)
    if not ch: return
    state = await fetch_app_state()
    if not state: return
    live = _live_month_key()
    complete = [k for k in _months_with_ip(state) if k < live]
    if not complete: return
    mkey = complete[-1]
    prod = _submitted_ap_by_agent(state, mkey)
    ipm = _net_map(state, mkey)
    rows = []
    for a, ap in prod.items():
        if str(a).lower() in IP_EXCLUDE or ap <= 0: continue
        ipv = ipm.get(a, 0.0)
        rows.append((a, ap, ipv, ipv / ap))
    if not rows: return
    rows.sort(key=lambda r: r[3])
    lines = [f"• **{a}** — ${int(ap):,} AP → ${int(ipv):,} IP · **{ntg*100:.0f}% NTG**"
             + (" ⚠️" if ntg < 0.5 else "")
             for a, ap, ipv, ntg in rows[:15]]
    team_ap = sum(r[1] for r in rows); team_ip = sum(r[2] for r in rows)
    e = discord.Embed(title=f"🔬 NTG Quality Report — {_month_label(mkey)}",
        description=(f"Team: ${int(team_ap):,} submitted → ${int(team_ip):,} issued "
                     f"(**{(team_ip/team_ap*100) if team_ap else 0:.0f}% NTG**)\n"
                     "Lowest stick-rates first — ⚠️ = under 50%:\n\n" + "\n".join(lines)),
        color=0xE23B3B)
    e.set_footer(text="Owner eyes only · submitted AP vs Gateway-issued IP")
    try: await ch.send(embed=e)
    except Exception as ex: print("ntg", ex)


# ---- scheduler ------------------------------------------------------------
_last_daily = None
_last_weekly = None
_last_team_ip = None
_last_monthly = None

@client.event
async def on_ready():
    print(f"Logged in as {client.user}.")
    await cloud_pull_state()                       # restore history BEFORE anything reads it
    load_state()
    try: sync_current_voice()                      # pick up anyone already mid-session
    except Exception as e: print("voice rescan", e)
    await ensure_start_message()
    await ensure_bot_avatar()
    try: await tree.sync(guild=discord.Object(GUILD_ID))
    except Exception as e: print("tree sync", e)
    await ensure_commands_message()
    if SUPABASE_KEY:
        try: await ensure_lead_roi_message()
        except Exception as e: print("lead roi msg", e)
        try: await refresh_team_ap_board()
        except Exception as e: print("team board", e)
        try: await refresh_accountability_board()
        except Exception as e: print("acct board", e)
    if not scheduler.is_running(): scheduler.start()
    if SUPABASE_KEY and not wins_poller.is_running(): wins_poller.start()
    if SUPABASE_KEY and not ip_poller.is_running(): ip_poller.start()

@tasks.loop(seconds=30)
async def scheduler():
    global _last_daily, _last_weekly, _last_monthly
    n = now_pt(); ensure_today()
    # 1st of the month, 10 AM PT — team IP board + trend chart + NTG quality report
    if SUPABASE_KEY and n.day == 1 and n.time() >= MONTHLY_TIME and _last_monthly != (n.year, n.month):
        _last_monthly = (n.year, n.month)
        await post_team_ip_monthly(); await post_trend_chart(); await post_ntg_report()
    if end_today() and n.time() >= end_today() and _last_daily != n.date() and scheduled_start_today() is not None:
        _last_daily = n.date(); await post_daily_report(); await post_deals_daily()
        await refresh_accountability_board()   # owner's bird's-eye board follows the daily close
    # Sunday 6 PM PT — the wrap (recognition + streak/PB recap + rank roles) and weekly boards
    if n.weekday() == WEEKLY_DAY and n.time() >= WEEKLY_TIME and _last_weekly != n.date():
        _last_weekly = n.date()
        await post_sunday_wrap()       # ONE public wrap + private accountability + roles
        await post_weekly_attendance() # detailed per-day week grid (owner + trainers)
        await post_quadrant()          # hours-vs-production quadrant (owner + trainers)
        await post_lead_roi()          # weekly lead-spend ROI scoreboard (public)
        await post_lead_report()       # lead-buying breakdown (owner-only)
        await refresh_team_ap_board()  # keep the live manager board fresh
    # IP Reports are import-driven (see ip_poller) — no fixed weekly/monthly schedule.


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set the DISCORD_TOKEN environment variable first (see README).")
    client.run(TOKEN)
