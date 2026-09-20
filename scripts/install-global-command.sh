#!/bin/sh
# Install /shek-apply as a USER-LEVEL Claude Code command (~/.claude/commands/),
# available from any directory. The wrapper pins this repo's absolute path and
# delegates to the repo's own command file, which stays the single source of truth.
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/.claude/commands"
mkdir -p "$DEST"

cat > "$DEST/shek-apply.md" <<EOF
---
description: Full job-application session (scrape, rank, tailor, review gate, report) in the job-hunt repo, runnable from any directory
---

# /shek-apply (global wrapper)

The job-hunt pipeline lives at: \`$REPO_ROOT\`

Rules for this entire session:

1. **Anchor everything to the repo.** Run EVERY Bash command prefixed with
   \`cd $REPO_ROOT && \` and read every referenced file by absolute path under
   that root. Never resolve pipeline paths against the current working directory.
2. First read \`$REPO_ROOT/CLAUDE.md\` — its "job-hunt Pipeline Contract"
   (never-submit, side effects only via publish_guard, DB is the only record,
   review gate) governs everything you do here.
3. Then read \`$REPO_ROOT/.claude/commands/shek-apply.md\` and follow it
   **exactly**. The user's arguments to this command are: \$ARGUMENTS
   Where it references other repo files
   (e.g. \`.claude/commands/apply.md\`, tools, skills), read them from
   \`$REPO_ROOT/\` and treat their relative paths the same way.
4. If the repo is missing at that path, say so and stop — do not guess
   another location.
EOF

echo "installed: $DEST/shek-apply.md (pinned to $REPO_ROOT)"
echo "restart Claude Code (any directory) and /shek-apply will be available"
