# /outcome — Record What Happened to an Application (job-hunt DB version)

`$ARGUMENTS`: an `application_id` or company name, optionally followed by what happened
(e.g. `optum interview`, `vtion rejected`). With nothing, list open applications and ask.

The DB is the only record (no `job_search_tracker.csv`). All writes go through
`uv run tools/tracker_db.py` or `uv run tools/mark_applied.py`.

## Step 1: Identify the application

```bash
uv run tools/tracker_db.py applied
uv run tools/tracker_db.py list
```

Match the argument to one application (company, case-insensitive). Ambiguous → ask, never
guess. Show its current state and latest outcome.

## Step 2: Collect what happened

Ask only what is missing: the outcome — `applied`, `interview` (which stage, date),
`offer`, `hired`, `rejected`, `no_response`, `offer_declined`, `withdrawn` — and a one-line
note (date, stage, who). `hired` / `offer_declined` / `withdrawn` are the user's own
decisions: record them only when the user states them.

## Step 3: Write

- **applied** (the user submitted): the application must be PUBLISHED.
  `uv run tools/mark_applied.py --application-id <id> --notes "<portal>, <date>"` — records it
  and deletes the resume PDFs (the `.tex` source stays). Not PUBLISHED yet → tell the user to
  approve it first (`make run ARGS="approve <id>"`, with an override if it was blocked).
- **anything else:**

```bash
uv run tools/tracker_db.py record-outcome <id> <outcome> --notes "<date> <note>" --source user
```

## Step 4: Next step

- `interview` → offer `/interview <id>` (prep pack) and `/outreach <id> thanks` after it.
- `applied` with no reply after FOLLOWUP_AFTER_DAYS → `/outreach <id> followup`.
- 3+ final outcomes recorded → suggest `/setup` calibration.
