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
    "tue_interval":  {"label": "Tuesday intervals",         "floor": 30,  "ceiling": 60,  "fixed": False, "current": 60},
    "wed_gym":       {"label": "Wednesday gym commute",     "floor": 35,  "ceiling": 35,  "fixed": True},
    "fri_gym":       {"label": "Friday gym commute",        "floor": 35,  "ceiling": 35,  "fixed": True},
    "long_run":      {"label": "Long Zone 2 (Sat or Sun)",  "floor": 63,  "ceiling": None, "fixed": False, "current": 89},
}

# Persistent state file — stores current targets between runs
STATE_FILE = os.path.join(os.path.dirname(__file__), "strava_state.json")


# ─────────────────────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    # First run — seed from SCHEDULE
    return {k: v.get("current", v["floor"]) for k, v in SCHEDULE.items()}


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
    """Fetch all activities from the most recently completed Mon 00:00 – Sun 23:59 UTC week.

    Anchored to the most recent Sunday on or before today, so the report covers the
    same full week no matter which day it runs. If the scheduled Sunday run fails, a
    manual re-run any day Mon–Sat still picks up that completed week (not the current,
    in-progress one).
    """
    today = datetime.now(timezone.utc).date()
    # Most recent Sunday on or before today (today itself if today is Sunday)
    days_since_sunday = (today.weekday() + 1) % 7
    last_sunday = today - timedelta(days=days_since_sunday)
    last_monday = last_sunday - timedelta(days=6)

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


# Only running (incl. treadmill/virtual) and football count toward volume.
CARDIO_TYPES = {
    "Run", "VirtualRun", "TrailRun",
    "Soccer",                                         # Football in Strava
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

    for a in activities:
        atype = a.get("sport_type") or a.get("type", "")  # sport_type is newer and covers types like Padel
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

        # Fetch HR zone breakdown from Strava for all cardio activities
        activity_id = a.get("id")
        if activity_id:
            zones = fetch_hr_zones(token, activity_id)
            for i in range(5):
                hr_zones[i] += zones[i]

    return {
        "total_cardio_mins": total_cardio_mins,
        "total_run_mins":    total_run_mins,
        "hr_zones":          hr_zones,
        "zone45_mins":       hr_zones[3] + hr_zones[4],
        "by_type":           by_type,
        "daily":             daily,
    }


# ─────────────────────────────────────────────
# TARGET ENGINE
# ─────────────────────────────────────────────
def compute_target_volume(targets):
    """Total target volume from current targets (sum of all sessions)."""
    return (
        targets["mon_gym"] +
        targets["tue_interval"] +
        targets["wed_gym"] +
        targets["fri_gym"] +
        targets["long_run"]
    )


def long_run_ceiling(total_target):
    """Long run ceiling = 180 mins (3h00)."""
    return 180


def adjust_targets(targets, actual_cardio_mins):
    """Apply the volume adjustment rules and return new targets.

    Hit 80% of target → everything adjustable goes up 10% (clamped to ceilings).
    No qualifying activity at all (no run/treadmill/football all week) →
    everything adjustable drops 10% (clamped to floors). Otherwise targets hold.
    """
    total_target = compute_target_volume(targets)
    threshold_high = round(total_target * 0.80)

    if actual_cardio_mins >= threshold_high:
        factor    = 1.10
        direction = "increase"
    elif actual_cardio_mins == 0:
        factor    = 0.90
        direction = "decrease"
    else:
        factor    = 1.00
        direction = "maintain"

    new_targets = dict(targets)

    if factor != 1.00:
        adjustable = ["tue_interval", "long_run"]
        for key in adjustable:
            s = SCHEDULE[key]
            new_val = targets[key] * factor

            # Clamp to floor/ceiling
            floor   = s["floor"]
            ceiling = s["ceiling"] or long_run_ceiling(total_target)
            new_val = max(floor, min(ceiling, round(new_val)))
            new_targets[key] = new_val

    return new_targets, direction, threshold_high


# ─────────────────────────────────────────────
# INTERVAL RECOMMENDATION — Norwegian 4×4
# ─────────────────────────────────────────────
def interval_recommendation(interval_target_mins, total_target_mins, actual_zone45_mins):
    """Prescribe a Norwegian 4×4 sized to the Tuesday interval target.

    Core block is fixed at 25 min (4 × 4 min hard + 3 × 3 min recovery jogs).
    Remaining session time goes to warm-up (capped at 15 min), then warm-down.
    """
    core_mins = 4 * 4 + 3 * 3  # 25 min
    remaining = max(interval_target_mins - core_mins, 0)
    warmup    = min(15, remaining)
    warmdown  = remaining - warmup

    session = (
        f"{warmup} min easy warm-up → "
        f"4 × 4 min in Zone 4-5 (~90% max HR) with 3 min easy jog recoveries → "
        f"{warmdown} min warm-down"
    )

    z45_target = round(total_target_mins * 0.20)

    if actual_zone45_mins > 0:
        if actual_zone45_mins >= z45_target:
            actual_note = f"✅ Last week you hit ~{actual_zone45_mins} mins in Z4-5 (target: {z45_target} mins). Great work — maintain intensity."
        else:
            gap = z45_target - actual_zone45_mins
            actual_note = f"⚠️ Last week: ~{actual_zone45_mins} mins in Z4-5 (target: {z45_target} mins). Try to close the {gap} min gap this week."
    else:
        actual_note = f"Target: {z45_target} mins in Zone 4-5 this week (~20% of total volume)."

    return session, actual_note, z45_target


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
                week_start, week_end, interval_session, interval_note, z45_target):

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
        badge_text  = "↓ Volume decreasing −10% — no runs logged last week"
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

    lr_note = f"ceiling {fmt(lr_ceiling)}"

    target_rows = (
        target_row("Monday — gym commute",        new_targets["mon_gym"]) +
        target_row("Tuesday — intervals",          new_targets["tue_interval"], changed=changed("tue_interval")) +
        target_row("Wednesday — gym commute",      new_targets["wed_gym"]) +
        target_row("Friday — gym commute",         new_targets["fri_gym"]) +
        target_row("Saturday/Sunday — long Zone 2",new_targets["long_run"], lr_note, changed("long_run"))
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
        80% threshold: {fmt(threshold_high)} · miss up to {fmt(compute_target_volume(old_targets) - threshold_high)} and still progress · −10% only if no runs all week
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
      <p style="margin:0 0 12px;font-size:12px;color:#999;text-transform:uppercase;letter-spacing:0.08em;">Next week targets</p>
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
      <p style="margin:0 0 12px;font-size:12px;color:#999;text-transform:uppercase;letter-spacing:0.08em;">Tuesday interval session — Norwegian 4×4</p>
      <p style="margin:0 0 8px;font-size:14px;color:#444;">{interval_note}</p>
      <p style="margin:0 0 8px;font-size:14px;color:#444;">Session length: <strong>{fmt(new_targets['tue_interval'])}</strong></p>
      <p style="margin:0;font-size:14px;color:#333;">{interval_session}</p>
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

    # REPORT_ONLY=1 → resend the email with current targets; no adjustment, no
    # state save. Safe to trigger manually after a week that was already processed.
    report_only = os.environ.get("REPORT_ONLY", "") == "1"
    if report_only:
        new_targets    = dict(old_targets)
        direction      = "maintain"
        threshold_high = round(compute_target_volume(old_targets) * 0.80)
    else:
        new_targets, direction, threshold_high = adjust_targets(old_targets, stats["total_cardio_mins"])

    total_target   = compute_target_volume(new_targets)
    interval_sess, interval_note, z45_target = interval_recommendation(
        new_targets["tue_interval"], total_target, stats["zone45_mins"]
    )

    html = build_email(
        stats, old_targets, new_targets, direction, threshold_high,
        week_start, week_end, interval_sess, interval_note, z45_target
    )

    send_email(html, week_start, week_end)
    if report_only:
        print("Report-only mode: state not saved.")
    else:
        save_state(new_targets)
        print("Targets saved.")


if __name__ == "__main__":
    main()
