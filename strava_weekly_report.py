#!/usr/bin/env python3
"""
Strava Weekly Running Report
Sends a Sunday 9pm email with last week's volume summary and next week's targets.
"""

import os
import json
import math
import smtplib
import requests
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# ─────────────────────────────────────────────
# CONFIG — fill these in
# ─────────────────────────────────────────────
STRAVA_CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID",     "YOUR_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
STRAVA_REFRESH_TOKEN = os.environ.get("STRAVA_REFRESH_TOKEN", "YOUR_REFRESH_TOKEN")

EMAIL_FROM    = os.environ.get("EMAIL_FROM",    "alex.wylie8888@gmail.com")
EMAIL_TO      = os.environ.get("EMAIL_TO",      "alex_wylie@hotmail.co.uk")
EMAIL_APP_PWD = os.environ.get("EMAIL_APP_PWD", "YOUR_GMAIL_APP_PASSWORD")

# ─────────────────────────────────────────────
# SCHEDULE & TARGETS (minutes)
# ─────────────────────────────────────────────
# Each session: { floor, ceiling, fixed, label }
# fixed=True → never adjusts
SCHEDULE = {
    "mon_gym":       {"label": "Monday gym commute",       "floor": 35,  "ceiling": 35,  "fixed": True},
    "tue_evie":      {"label": "Tuesday Zone 2 (Evie)",    "floor": 30,  "ceiling": 30,  "fixed": True},
    "tue_interval":  {"label": "Tuesday intervals",         "floor": 30,  "ceiling": 60,  "fixed": False, "current": 60},
    "wed_gym":       {"label": "Wednesday gym commute",     "floor": 35,  "ceiling": 35,  "fixed": True},
    "thu_combined":  {"label": "Thursday run (total)",      "floor": 30,  "ceiling": 60,  "fixed": False, "current": 50},
    "fri_gym":       {"label": "Friday gym commute",        "floor": 35,  "ceiling": 35,  "fixed": True},
    "long_run":      {"label": "Long Zone 2 (Sat or Sun)",  "floor": 63,  "ceiling": None, "fixed": False, "current": 103,
                      "note": "Evie portion 30–60 mins; top up after drop-off"},
}

# ─────────────────────────────────────────────
# EVIE SCHEDULE & TARGETS (minutes)
# ─────────────────────────────────────────────
EVIE_SCHEDULE = {
    "evie_tue":     {"label": "Tuesday (Evie)",          "floor": 30, "ceiling": 30,  "fixed": True,  "current": 30},
    "evie_thu":     {"label": "Thursday (Evie)",         "floor": 30, "ceiling": 60,  "fixed": False, "current": 45},
    "evie_weekend": {"label": "Saturday/Sunday (Evie)",  "floor": 30, "ceiling": 60,  "fixed": False, "current": 45},
}

# Persistent state file — stores current targets between runs
STATE_FILE = os.path.join(os.path.dirname(__file__), "strava_state.json")


# ─────────────────────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    # First run — seed from SCHEDULE and EVIE_SCHEDULE
    state = {k: v.get("current", v["floor"]) for k, v in SCHEDULE.items()}
    state.update({k: v.get("current", v["floor"]) for k, v in EVIE_SCHEDULE.items()})
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ─────────────────────────────────────────────
# STRAVA API
# ─────────────────────────────────────────────
def get_access_token():
    r = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "grant_type":    "refresh_token",
        "refresh_token": STRAVA_REFRESH_TOKEN,
    })
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_last_week_activities(token):
    """Fetch all activities from last Monday 00:00 to Sunday 23:59 UTC."""
    today = datetime.now(timezone.utc).date()
    # Last Monday
    days_since_monday = (today.weekday() + 7) % 7 or 7
    last_monday = today - timedelta(days=days_since_monday)
    last_sunday = last_monday + timedelta(days=6)

    after  = int(datetime(last_monday.year, last_monday.month, last_monday.day, tzinfo=timezone.utc).timestamp())
    before = int(datetime(last_sunday.year, last_sunday.month, last_sunday.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())

    activities = []
    page = 1
    while True:
        r = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"after": after, "before": before, "per_page": 100, "page": page}
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        activities.extend(batch)
        page += 1

    return activities, last_monday, last_sunday


CARDIO_TYPES = {
    "Run", "VirtualRun", "TrailRun",
    "Ride", "VirtualRide", "MountainBikeRide",
    "Swim", "Elliptical", "StairStepper", "Rowing",
    "Soccer",                                         # Football in Strava
    "AlpineSki", "NordicSki", "BackcountrySki",       # Skiing
}

RUN_TYPES = {"Run", "VirtualRun", "TrailRun"}


def fetch_hr_zones(token, activity_id):
    """Fetch time in each HR zone (Z1-Z5) for a single activity. Returns list of 5 values in minutes."""
    try:
        r = requests.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}/zones",
            headers={"Authorization": f"Bearer {token}"}
        )
        if r.status_code != 200:
            return [0, 0, 0, 0, 0]
        data = r.json()
        for zone_block in data:
            if zone_block.get("type") == "heartrate":
                buckets = zone_block.get("distribution_buckets", [])
                return [round(buckets[i]["time"] / 60) if i < len(buckets) else 0 for i in range(5)]
    except Exception:
        pass
    return [0, 0, 0, 0, 0]


def is_evie_run(activity):
    """Detect Evie runs by dog emoji in the activity name."""
    name = activity.get("name", "") or ""
    return "🐶" in name and activity.get("type", "") in RUN_TYPES


def analyse_activities(activities, token):
    total_cardio_mins = 0
    total_run_mins    = 0
    hr_zones          = [0, 0, 0, 0, 0]  # Z1-Z5 totals across all activities
    by_type           = {}
    daily             = {i: [] for i in range(7)}  # 0=Mon ... 6=Sun

    # Evie session tracking
    thu_evie_mins  = None
    thu_solo_mins  = None
    thu_total_mins = None
    lr_evie_mins   = None
    lr_solo_mins   = None
    lr_total_mins  = None
    tue_evie_mins  = None

    for a in activities:
        atype = a.get("type", "")
        if atype not in CARDIO_TYPES:
            continue

        mins  = round(a.get("moving_time", 0) / 60)
        name  = a.get("name", "") or ""
        evie  = is_evie_run(a)
        total_cardio_mins += mins
        by_type[atype] = by_type.get(atype, 0) + mins

        start = datetime.fromisoformat(a["start_date_local"].replace("Z", "+00:00"))
        day   = start.weekday()  # 0=Mon ... 6=Sun

        daily[day].append({
            "type": atype, "mins": mins, "name": name, "evie": evie
        })

        if atype in RUN_TYPES:
            total_run_mins += mins

            if day == 1:  # Tuesday
                if evie:
                    tue_evie_mins = mins
            elif day == 3:  # Thursday
                if evie:
                    thu_evie_mins = mins
                else:
                    thu_solo_mins = max(thu_solo_mins or 0, mins)
            elif day in (5, 6):  # Saturday or Sunday
                if evie:
                    lr_evie_mins = mins
                else:
                    lr_solo_mins = max(lr_solo_mins or 0, mins)

        # Fetch HR zone breakdown from Strava for all cardio activities
        activity_id = a.get("id")
        if activity_id:
            zones = fetch_hr_zones(token, activity_id)
            for i in range(5):
                hr_zones[i] += zones[i]

    # Compute combined totals
    if thu_evie_mins is not None or thu_solo_mins is not None:
        thu_total_mins = (thu_evie_mins or 0) + (thu_solo_mins or 0)
    if lr_evie_mins is not None or lr_solo_mins is not None:
        lr_total_mins = (lr_evie_mins or 0) + (lr_solo_mins or 0)

    return {
        "total_cardio_mins": total_cardio_mins,
        "total_run_mins":    total_run_mins,
        "hr_zones":          hr_zones,
        "zone45_mins":       hr_zones[3] + hr_zones[4],
        "by_type":           by_type,
        "daily":             daily,
        "tue_evie_mins":  tue_evie_mins,
        "thu_evie_mins":  thu_evie_mins,
        "thu_solo_mins":  thu_solo_mins,
        "thu_total_mins": thu_total_mins,
        "lr_evie_mins":   lr_evie_mins,
        "lr_solo_mins":   lr_solo_mins,
        "lr_total_mins":  lr_total_mins,
    }


# ─────────────────────────────────────────────
# TARGET ENGINE
# ─────────────────────────────────────────────
def compute_target_volume(targets):
    """Total target volume from current targets (sum of all sessions)."""
    return (
        targets["mon_gym"] +
        targets["tue_evie"] +
        targets["tue_interval"] +
        targets["wed_gym"] +
        targets["thu_combined"] +
        targets["fri_gym"] +
        targets["long_run"]
    )


def long_run_ceiling(total_target):
    """Long run ceiling = 33% of total weekly volume, hard cap 210 mins (3h30)."""
    return min(round(total_target * 0.33), 210)


def adjust_targets(targets, actual_cardio_mins):
    """Apply the volume adjustment rules and return new targets."""
    total_target = compute_target_volume(targets)
    threshold_high = round(total_target * 0.80)
    threshold_low  = 180  # 3 hours

    if actual_cardio_mins >= threshold_high:
        factor = 1.10   # +10%
        direction = "increase"
    elif actual_cardio_mins < threshold_low:
        factor = 0.90   # -10%
        direction = "decrease"
    else:
        factor = 1.00
        direction = "maintain"

    new_targets = dict(targets)

    if factor != 1.00:
        adjustable = ["tue_interval", "thu_combined", "long_run"]
        for key in adjustable:
            s = SCHEDULE[key]
            old = targets[key]
            raw = old * factor
            # Cap single-run change at 10%
            max_change = old * 0.10
            if factor > 1:
                new_val = min(old + max_change, raw)
            else:
                new_val = max(old - max_change, raw)

            # Clamp to floor/ceiling
            floor   = s["floor"]
            ceiling = s["ceiling"] or long_run_ceiling(total_target)
            new_val = max(floor, min(ceiling, round(new_val)))
            new_targets[key] = new_val

    return new_targets, direction, threshold_high


# ─────────────────────────────────────────────
# EVIE TARGET ENGINE
# ─────────────────────────────────────────────
def compute_evie_target_volume(targets):
    """Total of Evie's weekly target sessions."""
    return targets["evie_tue"] + targets["evie_thu"] + targets["evie_weekend"]


def adjust_evie_targets(targets, stats):
    """Adjust Evie's targets based on her actual volume last week."""
    evie_actual = (
        (stats["tue_evie_mins"] or 0) +
        (stats["thu_evie_mins"] or 0) +
        (stats["lr_evie_mins"]  or 0)
    )
    total_target   = compute_evie_target_volume(targets)
    threshold_high = round(total_target * 0.80)
    threshold_low  = round(total_target * 0.50)

    if evie_actual >= threshold_high:
        factor    = 1.10
        direction = "increase"
    elif evie_actual < threshold_low:
        factor    = 0.90
        direction = "decrease"
    else:
        factor    = 1.00
        direction = "maintain"

    new_targets = dict(targets)
    if factor != 1.00:
        for key in ["evie_thu", "evie_weekend"]:  # tue is fixed
            s       = EVIE_SCHEDULE[key]
            old_val = targets[key]
            change  = min(old_val * 0.10, abs(old_val * factor - old_val))
            if factor > 1:
                new_val = old_val + change
            else:
                new_val = old_val - change
            new_val = max(s["floor"], min(s["ceiling"], round(new_val)))
            new_targets[key] = new_val

    return new_targets, direction, evie_actual, threshold_high, threshold_low


# ─────────────────────────────────────────────
# INTERVAL RECOMMENDATIONS
# ─────────────────────────────────────────────
def interval_recommendation(interval_target_mins, total_target_mins, actual_zone45_mins):
    """Suggest an interval session structure targeting ~20% of weekly volume in Z4-5."""
    z45_target = round(total_target_mins * 0.20)
    work_mins  = round(interval_target_mins * 0.55)  # ~55% of session as hard work

    # HR zone descriptions (Garmin/standard 5-zone model)
    # Zone 4: 80-90% max HR — lactate threshold
    # Zone 5: 90-100% max HR — VO2max / neuromuscular

    if interval_target_mins >= 55:
        sessions = [
            f"5 × 6 min in Zone 4 (80–90% max HR), 90s easy jog recovery — {work_mins} min total hard work",
            f"4 × 8 min in Zone 4 (80–90% max HR), 2 min easy jog recovery — threshold focus",
            f"3 × 10 min in Zone 4 (80–90% max HR), 2 min recovery — sustained lactate threshold",
        ]
    elif interval_target_mins >= 40:
        sessions = [
            f"6 × 4 min in Zone 4-5 (80–95% max HR), 90s recovery — {work_mins} min total hard work",
            f"4 × 6 min in Zone 4 (80–90% max HR), 90s easy jog recovery — threshold focus",
            f"8 × 3 min in Zone 5 (90–100% max HR), 90s recovery — VO2max focus",
        ]
    else:
        sessions = [
            f"6 × 3 min in Zone 4-5 (80–95% max HR), 60s recovery",
            f"4 × 5 min in Zone 4 (80–90% max HR), 90s recovery",
            f"10 × 90s in Zone 5 (90–100% max HR), 60s recovery — short and sharp",
        ]

    actual_note = ""
    if actual_zone45_mins > 0:
        if actual_zone45_mins >= z45_target:
            actual_note = f"✅ Last week you hit ~{actual_zone45_mins} mins in Z4-5 (target: {z45_target} mins). Great work — maintain intensity."
        else:
            gap = z45_target - actual_zone45_mins
            actual_note = f"⚠️ Last week: ~{actual_zone45_mins} mins in Z4-5 (target: {z45_target} mins). Try to close the {gap} min gap this week."
    else:
        actual_note = f"Target: {z45_target} mins in Zone 4-5 this week (~20% of total volume)."

    return sessions, actual_note, z45_target


# ─────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────
def fmt(mins):
    h = mins // 60
    m = mins % 60
    if h and m:
        return f"{h}h {m}m"
    elif h:
        return f"{h}h"
    return f"{m}m"



def build_email(stats, old_targets, new_targets, direction, threshold_high,
                week_start, week_end, interval_sessions, interval_note, z45_target,
                old_evie, new_evie, evie_direction, evie_actual, evie_thresh_high, evie_thresh_low):

    total_target = compute_target_volume(old_targets)
    lr_ceiling   = long_run_ceiling(total_target)
    actual       = stats["total_cardio_mins"]
    pct          = round(actual / total_target * 100) if total_target else 0

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    # Build daily breakdown
    daily_rows = ""
    for i, day in enumerate(day_names):
        acts = stats["daily"].get(i, [])
        if acts:
            for a in acts:
                evie_tag = ' <span style="font-size:11px;color:#1D9E75;">🐶 Evie</span>' if a.get("evie") else ""
                daily_rows += f"""
                <tr>
                  <td style="padding:6px 12px;color:#666;font-size:14px;">{day}</td>
                  <td style="padding:6px 12px;font-size:14px;">{a['name'] or a['type']}{evie_tag}</td>
                  <td style="padding:6px 12px;text-align:right;font-size:14px;font-weight:500;">{fmt(a['mins'])}</td>
                </tr>"""
        else:
            daily_rows += f"""
                <tr>
                  <td style="padding:6px 12px;color:#ccc;font-size:14px;">{day}</td>
                  <td style="padding:6px 12px;color:#ccc;font-size:14px;">—</td>
                  <td style="padding:6px 12px;color:#ccc;font-size:14px;text-align:right;">—</td>
                </tr>"""

    # Direction badge
    if direction == "increase":
        badge_color = "#1D9E75"
        badge_text  = "↑ Volume increasing +10%"
    elif direction == "decrease":
        badge_color = "#D85A30"
        badge_text  = "↓ Volume decreasing −10%"
    else:
        badge_color = "#888"
        badge_text  = "→ Volume unchanged"

    # Next week targets
    def target_row(label, mins, note="", changed=False):
        change_style = "color:#1D9E75;font-weight:500;" if changed else ""
        note_html = f'<br><span style="font-size:12px;color:#999;">{note}</span>' if note else ""
        return f"""
        <tr>
          <td style="padding:8px 12px;font-size:14px;">{label}{note_html}</td>
          <td style="padding:8px 12px;text-align:right;font-size:14px;{change_style}">{fmt(mins)}</td>
        </tr>"""

    def changed(key):
        return new_targets[key] != old_targets[key]

    # Compute top-up durations (combined target minus Evie target)
    thu_topup  = new_targets["thu_combined"] - new_evie["evie_thu"]
    lr_topup   = new_targets["long_run"]     - new_evie["evie_weekend"]

    thu_note  = f"Evie {fmt(new_evie['evie_thu'])}"
    lr_note   = f"Evie {fmt(new_evie['evie_weekend'])} + {fmt(lr_topup)} top-up · ceiling {fmt(lr_ceiling)}"

    thu_changed = changed("thu_combined") or (new_evie["evie_thu"] != old_evie["evie_thu"])
    lr_changed  = changed("long_run")     or (new_evie["evie_weekend"] != old_evie["evie_weekend"])

    target_rows = (
        target_row("Monday — gym commute",        new_targets["mon_gym"]) +
        target_row("Tuesday — Zone 2 with Evie",  new_targets["tue_evie"]) +
        target_row("Tuesday — intervals",          new_targets["tue_interval"], changed=changed("tue_interval")) +
        target_row("Wednesday — gym commute",      new_targets["wed_gym"]) +
        target_row("Thursday — combined run",      new_targets["thu_combined"], thu_note, thu_changed) +
        target_row("Friday — gym commute",         new_targets["fri_gym"]) +
        target_row("Saturday/Sunday — long Zone 2",new_targets["long_run"], lr_note, lr_changed)
    )

    interval_options_html = "".join(
        f'<li style="margin-bottom:6px;">{s}</li>' for s in interval_sessions
    )

    # Evie target rows
    def evie_changed(key):
        return new_evie[key] != old_evie[key]

    if evie_direction == "increase":
        evie_badge = '<span style="font-size:12px;font-weight:500;color:#1D9E75;">↑ +10%</span>'
    elif evie_direction == "decrease":
        evie_badge = '<span style="font-size:12px;font-weight:500;color:#D85A30;">↓ −10%</span>'
    else:
        evie_badge = '<span style="font-size:12px;font-weight:500;color:#888;">→ unchanged</span>'

    evie_target_rows = (
        target_row("Tuesday", new_evie["evie_tue"]) +
        target_row("Thursday", new_evie["evie_thu"], changed=evie_changed("evie_thu")) +
        target_row("Saturday/Sunday", new_evie["evie_weekend"], changed=evie_changed("evie_weekend"))
    )

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:580px;margin:32px auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e8e8e8;">

    <!-- Header -->
    <div style="background:#1a1a1a;padding:28px 32px;">
      <p style="margin:0;font-size:12px;color:#888;letter-spacing:0.08em;text-transform:uppercase;">Weekly Running Report</p>
      <h1 style="margin:8px 0 0;font-size:22px;font-weight:500;color:#fff;">
        {week_start.strftime('%-d %b')} – {week_end.strftime('%-d %b %Y')}
      </h1>
    </div>

    <!-- Volume summary -->
    <div style="padding:24px 32px;border-bottom:1px solid #f0f0f0;">
      <table style="width:100%;border-collapse:collapse;">
        <tr>
          <td style="padding:0 8px 0 0;">
            <div style="background:#f7f7f7;border-radius:8px;padding:14px 16px;">
              <p style="margin:0;font-size:12px;color:#999;">Total cardio</p>
              <p style="margin:4px 0 0;font-size:24px;font-weight:500;color:#1a1a1a;">{fmt(actual)}</p>
            </div>
          </td>
          <td style="padding:0 8px;">
            <div style="background:#f7f7f7;border-radius:8px;padding:14px 16px;">
              <p style="margin:0;font-size:12px;color:#999;">vs target</p>
              <p style="margin:4px 0 0;font-size:24px;font-weight:500;color:#1a1a1a;">{pct}%</p>
            </div>
          </td>
          <td style="padding:0 0 0 8px;">
            <div style="background:#f7f7f7;border-radius:8px;padding:14px 16px;">
              <p style="margin:0;font-size:12px;color:#999;">Z4-5 time</p>
              <p style="margin:4px 0 0;font-size:24px;font-weight:500;color:#1a1a1a;">{fmt(stats['zone45_mins'])}</p>
            </div>
          </td>
        </tr>
      </table>

      <!-- HR zone breakdown -->
      <div style="margin-top:16px;">
        <p style="margin:0 0 8px;font-size:12px;color:#999;text-transform:uppercase;letter-spacing:0.08em;">Heart rate zones</p>
        <table style="width:100%;border-collapse:collapse;">
          <tr>
            {"".join([
              f'<td style="padding:0 4px 0 0;width:20%;">' +
              f'<div style="background:#f7f7f7;border-radius:6px;padding:8px 10px;">' +
              f'<p style="margin:0;font-size:11px;color:#999;">Zone {i+1}</p>' +
              f'<p style="margin:2px 0 0;font-size:15px;font-weight:500;color:#1a1a1a;">{fmt(stats["hr_zones"][i])}</p>' +
              f'</div></td>'
              for i in range(5)
            ])}
          </tr>
        </table>
      </div>

      <div style="margin-top:12px;display:inline-block;background:{badge_color}18;
                  color:{badge_color};font-size:13px;font-weight:500;
                  padding:5px 12px;border-radius:6px;">
        {badge_text}
      </div>
      <p style="margin:8px 0 0;font-size:13px;color:#999;">
        80% threshold: {fmt(threshold_high)} · miss up to {fmt(compute_target_volume(old_targets) - threshold_high)} mins and still progress · 3h floor
      </p>
    </div>

    <!-- Daily breakdown -->
    <div style="padding:24px 32px;border-bottom:1px solid #f0f0f0;">
      <p style="margin:0 0 12px;font-size:12px;color:#999;text-transform:uppercase;letter-spacing:0.08em;">Last week</p>
      <table style="width:100%;border-collapse:collapse;">
        {daily_rows}
      </table>
    </div>

    <!-- Next week targets -->
    <div style="padding:24px 32px;border-bottom:1px solid #f0f0f0;">
      <p style="margin:0 0 4px;font-size:12px;color:#999;text-transform:uppercase;letter-spacing:0.08em;">Next week targets</p>
      <p style="margin:0 0 12px;font-size:12px;color:#999;">
        Evie last week: {fmt(evie_actual)} · target {fmt(compute_evie_target_volume(old_evie))} · {evie_badge}
      </p>
      <table style="width:100%;border-collapse:collapse;">
        {target_rows}
        <tr style="border-top:1px solid #f0f0f0;">
          <td style="padding:10px 12px;font-size:14px;font-weight:500;">Total</td>
          <td style="padding:10px 12px;text-align:right;font-size:14px;font-weight:500;">{fmt(compute_target_volume(new_targets))}</td>
        </tr>
      </table>
    </div>

    <!-- Interval session -->
    <div style="padding:24px 32px;border-bottom:1px solid #f0f0f0;">
      <p style="margin:0 0 12px;font-size:12px;color:#999;text-transform:uppercase;letter-spacing:0.08em;">Tuesday interval session</p>
      <p style="margin:0 0 8px;font-size:14px;color:#444;">{interval_note}</p>
      <p style="margin:0 0 8px;font-size:14px;color:#444;">Session length: <strong>{fmt(new_targets['tue_interval'])}</strong>. Pick one of:</p>
      <ul style="margin:0;padding-left:20px;color:#333;">
        {interval_options_html}
      </ul>
      <p style="margin:12px 0 0;font-size:13px;color:#999;">
        Always bookend with 5–10 min easy warm-up and cool-down jog within the total session time.
      </p>
    </div>

    <!-- Footer -->
    <div style="padding:20px 32px;background:#fafafa;">
      <p style="margin:0;font-size:12px;color:#bbb;text-align:center;">
        Marathon-ready training plan · Generated {datetime.now().strftime('%-d %b %Y, %H:%M')}
      </p>
    </div>

  </div>
</body>
</html>"""

    return html


def send_email(html_body, week_start, week_end):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Running targets · w/c {(week_end + timedelta(days=1)).strftime('%-d %b %Y')}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_APP_PWD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print("Email sent.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print("Fetching Strava data...")
    token      = get_access_token()
    activities, week_start, week_end = fetch_last_week_activities(token)
    stats      = analyse_activities(activities, token)

    print(f"  Total cardio: {stats['total_cardio_mins']} mins across {len(activities)} activities")

    state       = load_state()
    old_targets = {k: state[k] for k in SCHEDULE if k in state}
    # Seed any missing keys
    for k, v in SCHEDULE.items():
        if k not in old_targets:
            old_targets[k] = v.get("current", v["floor"])
    new_targets, direction, threshold_high = adjust_targets(old_targets, stats["total_cardio_mins"])

    total_target   = compute_target_volume(new_targets)
    interval_sess, interval_note, z45_target = interval_recommendation(
        new_targets["tue_interval"], total_target, stats["zone45_mins"]
    )

    old_evie = {k: state[k] for k in EVIE_SCHEDULE if k in state}
    # Seed any missing Evie keys (first run after adding Evie)
    for k, v in EVIE_SCHEDULE.items():
        if k not in old_evie:
            old_evie[k] = v.get("current", v["floor"])

    new_evie, evie_direction, evie_actual, evie_thresh_high, evie_thresh_low = adjust_evie_targets(old_evie, stats)

    html = build_email(
        stats, old_targets, new_targets, direction, threshold_high,
        week_start, week_end, interval_sess, interval_note, z45_target,
        old_evie, new_evie, evie_direction, evie_actual, evie_thresh_high, evie_thresh_low
    )

    send_email(html, week_start, week_end)
    # Save both Alex and Evie targets
    combined = dict(new_targets)
    combined.update(new_evie)
    save_state(combined)
    print("Targets saved.")


if __name__ == "__main__":
    main()
