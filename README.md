# Strava Weekly Running Report

A Python script that runs automatically every Sunday at 9pm, pulls last week's Strava data, and emails a personalised training report to your inbox.

## What it does

- Pulls all cardio activities from the past week via the Strava API
- Summarises total volume, activity breakdown, and actual Zone 4-5 time (from your HR monitor data)
- Compares actual volume against your targets and adjusts next week's targets accordingly
- Tracks Evie's running targets separately and adjusts those too
- Suggests three interval session options for Tuesday based on HR zones
- Sends a formatted HTML email from Gmail to your Hotmail inbox every Sunday at 9pm

## Schedule

| Day | Session | Target | Notes |
|-----|---------|--------|-------|
| Monday | Gym commute | 35 min | Fixed |
| Tuesday | Zone 2 with Evie | 30 min | Fixed |
| Tuesday | Intervals | 30–60 min | Adjustable |
| Wednesday | Gym commute | 35 min | Fixed |
| Thursday | Combined run | 60–90 min | Evie portion + solo top-up |
| Friday | Gym commute | 35 min | Fixed |
| Sat/Sun | Long Zone 2 | 63 min+ | Evie portion + solo top-up |

## Target adjustment rules

Applied each Sunday based on last week's actual cardio volume:

| Last week's cardio | Outcome |
|--------------------|---------|
| ≥ 80% of target volume | +10% on adjustable sessions |
| < 3 hours (180 min) | −10% on adjustable sessions |
| Between those thresholds | No change |

Single-session change is capped at 10% regardless. Fixed sessions never change.

## Evie's adjustment rules

| Last week's Evie volume | Outcome |
|------------------------|---------|
| ≥ 80% of her target | +10% on Thursday and weekend sessions |
| < 50% of her target | −10% on Thursday and weekend sessions |
| Between those thresholds | No change |

Evie's Tuesday run is fixed at 30 min. Thursday and weekend sessions have a floor of 30 min and ceiling of 60 min.

## Cardio activity types counted

Runs, cycling (all variants), swimming, football (logged as Soccer in Strava), skiing (Alpine, Nordic, Backcountry), elliptical, rowing, stair stepper. Walking and hiking excluded.

## Evie run detection

Any run with 🐶 in the Strava activity name is identified as an Evie run. Thursday and weekend long runs should be recorded as two separate Strava activities — one with Evie (🐶 in the name), one solo top-up — so the script can validate each portion correctly.

## Zone 4-5 tracking

Zone 4-5 time is pulled directly from Strava's `/activities/{id}/zones` endpoint using your HR monitor data. Requires a HR monitor on every run. The interval session recommendation targets ~20% of weekly volume in Zone 4-5.

## Interval session recommendations

Three options are suggested each week based on the Tuesday interval target duration, using heart rate zones:
- **Zone 4 (80–90% max HR)** — lactate threshold / tempo focus
- **Zone 5 (90–100% max HR)** — VO2max / neuromuscular focus

Sessions always include warm-up and cool-down within the total session time.

## Long run ceiling

Dynamically calculated as 33% of total weekly volume, hard capped at 3h 30m. Grows automatically as weekly volume increases toward marathon-ready fitness (~600–650 min/week).

## Setup

### 1. Strava API credentials

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api) and create an app
2. Run `strava_auth_setup.py` locally to get a refresh token with `activity:read_all` scope
3. Note your Client ID, Client Secret, and Refresh Token

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

Place `strava_report.yml` in `.github/workflows/` in the repository root. The workflow runs automatically every Sunday at 21:00 UTC.

## State file

`strava_state.json` is created automatically on first run and stores current targets for both Alex and Evie week to week. It lives in the repository root and is committed back to the repo after each run if you add a commit step — otherwise it resets each week. To persist state across runs, the file needs to be stored externally (e.g. as a GitHub Actions artifact or committed back to the repo).

## BST note

The workflow runs at `21:00 UTC`. In winter (GMT) this is 9pm. When British Summer Time begins (last Sunday of March), UTC+1 means the email arrives at 10pm. Update the cron to `0 20 * * 0` each March and back to `0 21 * * 0` each October.

## Files

| File | Purpose |
|------|---------|
| `strava_weekly_report.py` | Main script |
| `strava_auth_setup.py` | One-time local setup to get Strava refresh token |
| `.github/workflows/strava_report.yml` | GitHub Actions workflow |
| `strava_state.json` | Auto-created — stores current targets week to week |
| `README.md` | This file |
