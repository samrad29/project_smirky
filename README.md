# Run Coach — starter v0.1

A small, local-first prototype: activity JSON + planned workout + athlete profile + recent history -> structured AI review. For a back-and-forth session, run `python -m run_coach.chat`.

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

## 3. Talk to the coach

```bash
python -m run_coach.chat
```

The session starts with a greeting. If `GARMIN_EMAIL` and `GARMIN_PASSWORD` are set, it downloads Garmin activities that are newer than the latest activity already in `data/activities/` and drops those FIT files into `data/FIT_files/`. It does not download the whole account. It then checks that folder for FIT files that have not been imported. Archived files in `old_files/` are ignored. You can import those files before talking about the latest workouts in `data/activities/`.

The conversation is appended to `data/conversations/<athlete-id>.jsonl`. The coach can propose a change to one allowed profile field, but nothing is saved until you confirm it. Applied and declined proposals are appended to `data/profile_changelog.jsonl`. Inspect either one from the prompt:

```text
/profile
/log
/workouts
/inbox
/import
/quit
```

`python -m run_coach.chat --show-profile` and `python -m run_coach.chat --log` print those without starting a conversation. Profile updates do not change the training plan, and limitations cannot be stored as a diagnosis.

When Supabase credentials are set, new messages and profile changes are also inserted after you run [supabase/migrations/002_conversations.sql](supabase/migrations/002_conversations.sql). The JSONL files remain the local copy.

## 4. Import FIT activities

Drop new `.fit` files into `data/FIT_files/`. The importer reads the device session and lap summaries. It does not store GPS coordinates.

```bash
python -m run_coach.fit_import
```

Each file that imports successfully is written to `data/activities/<activity-id>.json`, then moved to `data/FIT_files/old_files/` so the next run does not pick it up again. A file that fails to parse stays in `data/FIT_files/` for a retry. Use the new JSON file as the `--activity` input for the review command.

Splits are the laps recorded on the device, so a manual lap button during a workout becomes one split. Distance is the device distance in miles. Pace is minutes per mile from timer time, which excludes pauses; elapsed time is stored beside it. Compare these against the watch summary before trusting them. The file may not include weather, perceived effort, or a written workout name. Do not commit FIT files or normalized activities that contain private training data.

## 5. Replace the sample data

- `data/athlete_profile.json`: replace with your goals, training background, constraints and coaching preferences.
- `data/plans/sample_plan.json`: replace with the workout you actually intended to do.
- `data/activities/sample_activity.json`: replace with one real activity normalized into the schema below.
- `data/history/sample_history.json`: add a few recent comparable workouts.

The sample activity is synthetic, not your real training data.

## 6. Input schema (v0.1)

Activity:
- `activity_id`, `started_at`, `sport`, `distance_mi`, `duration_s`
- optional `elevation_gain_ft`, `avg_hr_bpm`, `max_hr_bpm`, `perceived_effort_1_10`, `conditions`, `splits`, `notes`
- split fields: `split`, `start_mi`, `end_mi`, `distance_mi`, `duration_s`, `pace_per_mile`, optional `elapsed_s`, `avg_hr_bpm`, `max_hr_bpm`, `lap_trigger`

Plan:
- `date`, `session_type`, `purpose`, `prescription`, `target`, `next_sessions`

History is a list of prior activity summaries. Missing values should be `null` or omitted; do not invent them.

## 7. What this prototype deliberately does not do

- No Strava API integration.
- No automatic training-plan changes.
- No medical diagnosis or injury clearance.
- No email delivery yet; first verify that the review quality is good.
- No login or hosted app. Supabase is an optional store for this local script.

## 8. Scenario tests

Synthetic scenarios live in `tests/scenarios/`. They cover a workout cut short, a run stopped for pain, an easy day run too hard, and a strong half marathon. They do not use private FIT imports.

```bash
pytest
```

That command checks that each scenario builds a valid coach context. It does not call the model.

To score how the coach actually responds, set `RUN_COACH_EVALS=1` in the shell (the OpenAI key still comes from `.env`) and run:

```bash
pytest -m llm
```

Those tests call the model once per scenario and check the structured review: flags, a follow-up when the case needs one, and clear misses such as diagnosing an injury, prescribing a makeup workout, or turning a good race into another hard day. Wording can vary, so a failure prints the review.

## 9. Supabase

JSON files are still how activities enter the app, including FIT import. After a successful review, the script also stores the athlete, activity, planned session, and review when both variables are set in `.env`:

```
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

Use the service role key. It bypasses row level security, so keep it in `.env` and never commit it or put it in a frontend. If the variables are empty, the review is only written under `output/`.

Create a Supabase project, open the SQL editor, and run [supabase/migrations/001_initial.sql](supabase/migrations/001_initial.sql) once. For the conversational CLI, also run [supabase/migrations/002_conversations.sql](supabase/migrations/002_conversations.sql). Then fill in `.env`.

To build the coach history from stored activities instead of a history file:

```bash
python -m run_coach.review \
  --activity data/activities/sample_activity.json \
  --plan data/plans/sample_plan.json \
  --profile data/athlete_profile.json \
  --history-from-supabase
```

The current activity is left out of that history. `--history` remains the default.

## 10. Next milestones

1. Test on 5–10 real activities and revise the prompt/schema.
2. Add robust activity-file import and duplicate detection.
3. Add memory retrieval and adaptive planning on top of the saved conversation and profile log.
4. Add auth and a hosted app on top of the Supabase store.
5. Add email delivery after the review is reliable.
6. Evaluate a permitted activity data source before building a live integration.
