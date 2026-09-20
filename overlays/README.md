# overlays/

Our patches on top of the vendored ai-job-search framework. This directory **mirrors
target paths** (e.g. `overlays/.claude/commands/apply.md` replaces `.claude/commands/apply.md`)
and is applied LAST by `make sync`, so our files always win.

Rules:
- Every overlay that replaces an upstream file must have an entry in
  `overlays.lock.json`: `{ "<path>": "<sha256 of the upstream file it was written against>" }`.
  When upstream drifts, `make sync` flags the conflict instead of silently keeping a stale patch.
- Files with no upstream counterpart (new commands like `stats.md`) need no lock entry.
- After resolving a conflict, refresh the hash:
  `shasum -a 256 packages/ai-job-search/<path>`
