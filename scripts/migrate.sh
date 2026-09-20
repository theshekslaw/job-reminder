#!/bin/sh
# Apply db/migrations/*.sql in order, skipping versions already recorded
# in schema_migrations. Runs psql inside the compose db container so no
# local psql install is needed.
set -eu

cd "$(dirname "$0")/.."

if ! docker compose ps db --status running --quiet | grep -q .; then
  echo "migrate: database is not running — start it with 'make db-up'" >&2
  exit 1
fi

PSQL="docker compose exec -T db psql -v ON_ERROR_STOP=1 -U jobhunt -d jobhunt"

# Bootstrap the tracking table so the applied-version query works on a fresh DB.
$PSQL -q -c "CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());"

for f in db/migrations/*.sql; do
  version=$(basename "$f" .sql)
  applied=$($PSQL -tA -c "SELECT 1 FROM schema_migrations WHERE version='$version'")
  if [ "$applied" = "1" ]; then
    echo "migrate: $version already applied"
  else
    echo "migrate: applying $version"
    $PSQL -q < "$f"
  fi
done

echo "migrate: done"
