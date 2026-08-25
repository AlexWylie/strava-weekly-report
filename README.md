# Strava Weekly Running Report

A Python script that runs automatically every Sunday at 9pm, pulls last week's Strava data, and emails a personalised training report to your inbox.

## What it does

- Pulls all qualifying activities (running, treadmill, football) from the last completed Mon–Sun week via the Strava API
- Summarises total volume, activity breakdown, and actual Zone 4-5 time (from your HR monitor data)
- Compares actual volume against your targets and adjusts next week's targets
- Prescribes a Norwegian 4×4 interval session for Tuesday, sized to the interval target
- Sends a formatted HTML email from Gmail to your Hotmail inbox every Sunday at 9pm

## Schedule

| Day | Session | Target | Ceiling | Notes |
|-----|---------|--------|---------|-------|
| Monday | Gym commute | 35 min | — | Fixed |
| Tuesday | Intervals | adjustable | 60 min | Norwegian 4×4 |
| Wednesday | Gym commute | 35 min | — | Fixed |
| Thursday | Cycling | — | — | Not tracked — cycling doesn't count toward volume |
| Friday | Gym commute | 35 min | — | Fixed |
| Sat/Sun | Long Zone 2 (solo) | adjustable | 180 min | Marathon long-run cap |

Current targets live in `strava_state.json`.

## Target adjustment rules

Applied each Sunday based on last week's actual qualifying volume:

| Last week | Outcome |
|-----------|---------|
| ≥ 80% of target volume | +10% on adjustable sessions (clamped to ceilings) |
| No qualifying activity at all | −10% on adjustable sessions (clamped to floors) |
| Anything in between | No change |

## Activity types counted

Only running (including treadmill and virtual runs) and football (logged as Soccer in Strava) count toward volume. Everything else — cycling, Peloton, swimming, padel, gym work — is excluded from the totals and the daily log. The gym commutes in the schedule are runs, so they count.

Activity type is read from Strava's `sport_type` field (falling back to the legacy `type` field).

## Evie run detection

Any run with 🐶 in the Strava activity name is tagged "Evie" in the email's daily log. This is cosmetic only — no targets depend on it.

## Zone 4-5 tracking

Zone 4-5 time is pulled directly from Strava's `/activities/{id}/zones` endpoint using your HR monitor data. Requires a HR monitor on every run. The weekly Z4-5 target is ~20% of total volume.

## Tuesday interval session — Norwegian 4×4

The email prescribes one session: **4 × 4 min in Zone 4-5 (~90% max HR) with 3 min easy jog recoveries** — a fixed 25-minute core block. The remainder of the session target is allocated to a warm-up (capped at 15 min), with whatever is left as warm-down.

## Reporting window

The report always covers the most recently completed Mon–Sun week, anchored to the last Sunday on or before the run date. If the scheduled Sunday run fails, re-running the workflow any day Mon–Sat still reports the correct (completed) week.

## Manual runs and report-only mode

The workflow can be triggered manually from the Actions tab. Two modes:

- **Default** — full run: fetch, adjust targets, email, save state. Use this to recover a failed Sunday run.
- **Report only** (checkbox on the manual trigger) — emails the report with current targets, without adjusting or saving anything. Use this to resend a week that was already processed; a default re-run of a processed week would apply the +10% twice.

## Setup

### 1. Strava API credentials

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api) and create an app
2. Run `strava_auth_setup.py` locally to get a refresh token with `activity:read_all` scope
3. Note your Client ID, Client Secret, and Refresh Token

⚠️ Each run of `strava_auth_setup.py` invalidates the previous refresh token. Run it once, verify with `verify_strava.py` (should print HTTP 200), then update the GitHub secret — and don't run the auth script again.

### 2. Gmail app password

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
2. Enable 2-Step Verification
3. Search for "App passwords" and create one named "Strava Report"
4. Copy the 16-character password

### 3. GitHub repository secrets

Add these six secrets under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|--------|-------|
| `STRAVA_CLIENT_ID` | Your Strava app Client ID |
| `STRAVA_CLIENT_SECRET` | Your Strava app Client Secret |
| `STRAVA_REFRESH_TOKEN` | Refresh token with `activity:read_all` scope |
| `EMAIL_FROM` | Gmail address to send from |
| `EMAIL_TO` | Email address to send to |
| `EMAIL_APP_PWD` | Gmail app password |

### 4. Workflow file

`.github/workflows/strava_report.yml` runs the report automatically every Sunday at 21:00 UTC and commits the updated state back to the repo.

## State file

`strava_state.json` stores current targets week to week. It lives in the repository root and is committed back by the workflow after each scheduled run (report-only runs don't touch it).

## Troubleshooting

- **403 from `strava.com/oauth/token`** — the refresh token is dead (often caused by re-running the auth setup). Re-authorise once and update the secret; verify locally with `verify_strava.py` first.
- **Workflow failed on Sunday** — re-run it manually (default mode) any day before the next Sunday; it will still process the correct week.

## BST note

The workflow runs at `21:00 UTC`. In winter (GMT) this is 9pm. When British Summer Time begins (last Sunday of March), UTC+1 means the email arrives at 10pm. Update the cron to `0 20 * * 0` each March and back to `0 21 * * 0` each October.

## Files

| File | Purpose |
|------|---------|
| `strava_weekly_report.py` | Main script |
| `strava_auth_setup.py` | One-time local setup to get Strava refresh token |
| `verify_strava.py` | Local diagnostic — checks Strava credentials work before updating secrets |
| `.github/workflows/strava_report.yml` | GitHub Actions workflow |
| `strava_state.json` | Stores current targets week to week |
| `README.md` | This file |
