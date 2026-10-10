#!/usr/bin/env bash
# Backup + restore drill (V4 Stage E): dump the database, restore it into a scratch database, and prove the copy
# is usable: same row counts in every table, append-only triggers present, credit ledger reconciles.
# Usage: DATABASE_URL=postgresql://user:pass@host:port/db infra/scripts/backup_restore_check.sh
set -euo pipefail
: "${DATABASE_URL:?set DATABASE_URL}"
SCRATCH="restore_check_$(date +%s)"
ADMIN_URL="${DATABASE_URL%/*}/postgres"
DUMP="$(mktemp -d)/backup.dump"
cleanup() { psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null 2>&1 || true; rm -f "$DUMP"; }
trap cleanup EXIT

start=$(date +%s)
pg_dump --format=custom --no-owner --file="$DUMP" "$DATABASE_URL"
echo "dump: $(du -h "$DUMP" | cut -f1) in $(( $(date +%s) - start ))s"
psql "$ADMIN_URL" -qc "CREATE DATABASE $SCRATCH"
RESTORE_URL="${DATABASE_URL%/*}/$SCRATCH"
start=$(date +%s)
pg_restore --no-owner --exit-on-error --dbname="$RESTORE_URL" "$DUMP"
echo "restore: $(( $(date +%s) - start ))s"

counts() {
  psql "$1" -Atc "SELECT string_agg(t || '=' || n, ' ' ORDER BY t) FROM (
    SELECT table_name t, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I', table_name),
           false, true, '')))[1]::text::bigint n
    FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE') x"
}
a=$(counts "$DATABASE_URL"); b=$(counts "$RESTORE_URL")
[ "$a" = "$b" ] || { echo "ROW COUNTS DIFFER"; diff <(tr ' ' '\n' <<<"$a") <(tr ' ' '\n' <<<"$b"); exit 1; }
echo "row counts: identical ($(wc -w <<<"$a") tables)"

triggers=$(psql "$RESTORE_URL" -Atc "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_%_immutable'")
[ "$triggers" -ge 7 ] || { echo "append-only triggers missing ($triggers)"; exit 1; }
echo "append-only triggers: $triggers"

bad=$(psql "$RESTORE_URL" -Atc "SELECT count(*) FROM (SELECT user_id, sum(delta) s,
  (array_agg(balance_after ORDER BY id DESC))[1] last FROM credit_ledger GROUP BY user_id) x WHERE s <> last")
[ "$bad" = "0" ] || { echo "ledger does not reconcile for $bad users"; exit 1; }
echo "credit ledger: reconciles"
echo "BACKUP/RESTORE OK"
