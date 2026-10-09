# Run Coach — starter v0.1

A small, local-first prototype: activity JSON + planned workout + athlete profile + recent history -> structured AI review.

## 1. Setup

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# macOS/Linux: cp .env.example .env
```

Put your OpenAI API key in `.env`. Never commit `.env` or activity files containing private location data.

## 2. Run the sample

```bash
python -m run_coach.review \
  --activity data/activities/sample_activity.json \
  --plan data/plans/sample_plan.json \
  --profile data/athlete_profile.json \
  --history data/history/sample_history.json \
  --dry-run
```

`--dry-run` validates inputs and prints the exact context packet without calling an AI model.

Then run the actual review:

```bash
python -m run_coach.review \
  --activity data/activities/sample_activity.json \
  --plan data/plans/sample_plan.json \
  --profile data/athlete_profile.json \
  --history data/history/sample_history.json
```

The result is saved under `output/`. The program does not email anything or change the training plan.

## 3. Import FIT activities

Drop new `.fit` files into `data/FIT_files/`. The importer reads the device session and lap summaries. It does not store GPS coordinates.

```bash
python -m run_coach.fit_import
```

Each file that imports successfully is written to `data/activities/<activity-id>.json`, then moved to `data/FIT_files/old_files/` so the next run does not pick it up again. A file that fails to parse stays in `data/FIT_files/` for a retry. Use the new JSON file as the `--activity` input for the review command.

Splits are the laps recorded on the device, so a manual lap button during a workout becomes one split. Distance is the device distance in miles. Pace is minutes per mile from timer time, which excludes pauses; elapsed time is stored beside it. Compare these against the watch summary before trusting them. The file may not include weather, perceived effort, or a written workout name. Do not commit FIT files or normalized activities that contain private training data.

## 4. Replace the sample data

- `data/athlete_profile.json`: replace with your goals, training background, constraints and coaching preferences.
- `data/plans/sample_plan.json`: replace with the workout you actually intended to do.
- `data/activities/sample_activity.json`: replace with one real activity normalized into the schema below.
- `data/history/sample_history.json`: add a few recent comparable workouts.

The sample activity is synthetic, not your real training data.

## 5. Input schema (v0.1)

Activity:
- `activity_id`, `started_at`, `sport`, `distance_mi`, `duration_s`
- optional `elevation_gain_ft`, `avg_hr_bpm`, `max_hr_bpm`, `perceived_effort_1_10`, `conditions`, `splits`, `notes`
- split fields: `split`, `start_mi`, `end_mi`, `distance_mi`, `duration_s`, `pace_per_mile`, optional `elapsed_s`, `avg_hr_bpm`, `max_hr_bpm`, `lap_trigger`

Plan:
- `date`, `session_type`, `purpose`, `prescription`, `target`, `next_sessions`

History is a list of prior activity summaries. Missing values should be `null` or omitted; do not invent them.

## 6. What this prototype deliberately does not do

- No Strava API integration.
- No automatic training-plan changes.
- No medical diagnosis or injury clearance.
- No database yet; JSON files keep the first iteration easy to inspect.
- No email delivery yet; first verify that the review quality is good.

## 7. Next milestones

1. Test on 5–10 real activities and revise the prompt/schema.
2. Add robust activity-file import and duplicate detection.
3. Persist normalized activities, plans, reviews and decisions in Postgres.
4. Add email delivery after the review is reliable.
5. Evaluate a permitted activity data source before building a live integration.
