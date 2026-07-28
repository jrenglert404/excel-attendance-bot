# Excel Financial — Attendance & Culture Bot (v2)

One bot that runs the floor. All IDs for your server are already filled into
`attendance_bot.py` — you only supply the token.

## What it does

**Verification**
- Posts a **Request Access** button in `#start-here` (auto-posted on first run).
- New joiner clicks it → you get an **Approve / Deny** card in your private
  `#access-requests` → one click grants the Verified Member role.

**Daily attendance — PRIVATE, posted to `#attendance-log` (owner eyes only)**
- Real-time clock-ins: `🟢 on time` or `🔴 LATE` with arrival times.
- 6:00 PM PT report: late list, left-before-6 list, full-day list, no-shows,
  and the day's violation-point ranking.

**Camera tracking** — the bot reads Discord's camera state, so every report shows
who kept their camera on. Anyone under **50% camera-on** (after 30+ min in a room)
is flagged daily and weekly. Camera enforces your #1 culture rule automatically.

**Auto-flagging** — when the 6 PM report runs, anyone with **3+ late** or **2+ early
leaves** in the trailing 7 days gets a 🚨 flag that pings you directly.

**Weekly rollup — Saturdays at 10:00 AM PT**
- **PUBLIC** (`#weekly-report`): Top Hours + Perfect-Week shout-outs.
- **PRIVATE** (`#attendance-log`): Top Violators + at-risk (consistency < 60%).

Violation points count **only late arrivals and early departures** — arriving early
is never penalized, and no-shows are listed for awareness but don't add points.

Schedule (Pacific, auto PST/PDT):
- Start: **8:30** Mon/Thu (team huddle), **9:00** Tue/Wed/Fri/Sat (call session). Sun off.
- End: **6:00 PM** Mon–Fri, **2:00 PM** Saturday.

AFK time is never counted. "Left early" = final exit before that day's end time (lunch is fine).
Note: the 8:00 AM Thursday Conner White Training is first-90-day agents only, so the
general team's Thursday start is the 8:30 huddle. Adjust `SCHEDULE`/`END_BY_DAY` at the
top of the file if any of this changes.

---

## Step 1 — Create the bot & token
1. <https://discord.com/developers/applications> → **New Application** → name it "Excel Bot".
2. **Bot** tab → under **Privileged Gateway Intents** enable **SERVER MEMBERS INTENT** → Save.
3. **Reset Token** → copy it (keep it secret).

## Step 2 — Invite it (needs Manage Roles for verification)
Replace `YOUR_CLIENT_ID` with the Application ID (General Information page):
```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=268520448&scope=bot
```
That grants View, Send, Embed Links, Read History, **and Manage Roles**.

**Important:** after it joins, drag the bot's role **above** "✅・Verified Member"
in Server Settings → Roles. A bot can only grant roles below its own.

## Step 3 — Run it 24/7
Same as before — **Railway** (free, no computer) is easiest:
1. <https://railway.app> → new project from these files.
2. Variables → `DISCORD_TOKEN` = your token.
3. It installs `requirements.txt` and runs `python attendance_bot.py` automatically.

(Or Replit with a Secret, or any always-on machine: `pip install -r requirements.txt`,
set `DISCORD_TOKEN`, `python attendance_bot.py`.)

The bot writes `attendance_state.json` and `attendance_history.json` next to itself —
keep those with the bot so weekly rollups have history to aggregate.

---

**Tuning (top of `attendance_bot.py`):** `SCHEDULE`, `END_OF_DAY`, the violation
weights, and the at-risk threshold are all one-line edits. Tell me what you want
changed and I'll adjust it for you.
