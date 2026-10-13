# Strava Weekly Running Report

A Python script that runs automatically every Sunday at 9pm, pulls last week's Strava data, and emails a personalised training report to your inbox.

## What it does

- Pulls all qualifying activities (running, treadmill, football) from the last completed Mon–Sun week via the Strava API
- Summarises total volume, activity breakdown, and actual Zone 4-5 time (from your HR monitor data)
- Compares actual volume against your targets and adjusts next week's targets
- Session prescriptions (Friday tempo, Tuesday 4×4 fallback) are documented below, not printed in the email
- Stages new sessions into the plan one at a time, each at 10% of the weekly total
- Sends a formatted HTML email from Gmail to your Hotmail inbox every Sunday at 9pm

## Schedule

| Day | Session | Target | Ceiling | Notes |
|-----|---------|--------|---------|-------|
| Monday | Gym commute | 35 min | — | Fixed |
| Tuesday morning | Easy run | staged — 2nd | 60 min | Easy, under 140 bpm |
| Tuesday lunch | Easy run | staged — 3rd | 60 min | Easy, under 140 bpm |
| Tuesday evening | Football, or 4×4 if cancelled | adjustable | 60 min | One quality session |
| Wednesday | Gym commute | 35 min | — | Fixed |
| Thursday | Cycling | — | — | Not tracked — cycling doesn't count toward volume |
| Friday | Gym commute | 35 min | — | Fixed |
| Friday lunch | **Tempo** | 35 now, adjustable | 60 min | WFH |
| Saturday | Rest | — | — | |
| Sunday | Long Zone 2 (solo) | adjustable | 180 min | Marathon long-run cap |

Current targets live in `strava_state.json`.

## Target adjustment rules

Applied each Sunday based on last week's actual qualifying volume:

| Last week | Outcome |
|-----------|---------|
| ≥ 80% of target volume | +10% on adjustable sessions (clamped to ceilings) |
| No qualifying activity at all | −10% on adjustable sessions (clamped to floors) |
| Anything in between | No change |

**Staging.** New sessions don't all start at once. `strava_state.json` holds a `pending` list; on a qualifying week the next session is admitted at 10% of the weekly total instead of the ratchet running — that week's whole 10% allowance goes to the new run. Once nothing is pending, the normal +10% applies to every adjustable session. Current order: Friday tempo (in), then Tuesday morning, then Tuesday lunch.

## Activity types counted

Only running (including treadmill and virtual runs) and football (logged as Soccer in Strava) count toward volume. Everything else — cycling, Peloton, swimming, padel, gym work — is excluded from the totals and the daily log. The gym commutes in the schedule are runs, so they count.

Activity type is read from Strava's `sport_type` field (falling back to the legacy `type` field).

## Evie run detection

Any run with 🐶 in the Strava activity name is tagged "Evie" in the email's daily log. This is cosmetic only — no targets depend on it.

## Zone 4-5 tracking

Zone 4-5 time is pulled directly from Strava's `/activities/{id}/zones` endpoint using your HR monitor data. Requires a HR monitor on every run. The weekly Z4-5 target is ~20% of total volume.

## Friday lunch — tempo

Warm-up and cool-down take ~25% each (capped at 15 min); the block is the rest. At 35 that's 9 / 17 / 9; at 60 it's 15 / 30 / 15. The block is run by heart rate — **167–172 bpm**, comfortably hard — not by pace.

## Tuesday — Norwegian 4×4 fallback

Tuesday is one 60-minute quality slot: football when it's on, otherwise this session (not printed in the email): **4 × 4 min in Zone 4-5 (~90% max HR) with 3 min easy jog recoveries** — a fixed 25-minute core block. The remainder of the session target is allocated to a warm-up (capped at 15 min), with whatever is left as warm-down.

## Reporting window

The report always covers the most recently completed Mon–Sun week, anchored to the last Sunday on or before the run date. If the scheduled Sunday run fails, re-running the workflow any day Mon–Sat still reports the correct (completed) week.

## Manual runs and report-only mode

The workflow can be triggered manually from the Actions tab. Two modes:

- **Default** — full run: fetch, adjust targets, email, save state. Use this to recover a failed Sunday run.
- **Report only** (checkbox on the manual trigger) — emails the report with current targets, without adjusting or saving anything. Use this to resend a week that was already processed; a default re-run of a processed week would apply the +10% twice.

## Marathon plan

Target: **Sunday 18 April 2027, 3:30** (4:58/km). Strava's model predicted ~4:19 in Oct 2026, and the 1:38 half anchoring the goal was run at roughly 2.4× that fitness score — so 3:30 is the stretch target and 3:45 the realistic one unless the build goes well.

| Weeks | Dates | Phase |
|-------|-------|-------|
| 1–9 | 12 Oct – 13 Dec 2026 | Base — stage in Friday tempo, Tuesday morning, Tuesday lunch; then ratchet |
| 10–11 | 14 – 27 Dec | Hold — no increases over Christmas |
| 12–23 | 28 Dec – 21 Mar 2027 | Build — peak volume ~7h/week |
| 24–26 | 22 Mar – 11 Apr | Taper — cut volume ~25% / 40% / 55% by hand in `strava_state.json` |
| 27 | 12 – 18 Apr | Race |

**Checkpoint:** a half marathon between 24 Jan and 21 Feb. ~1:40–1:42 confirms 3:30; ~1:48+ means retarget to 3:45.

**Known compromises, by choice:** the long run is 3h every week with no cutback weeks; Friday's tempo caps at 60; Tuesday carries up to three runs including football; Thursday is a ride-only day and cycling is not counted.

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
