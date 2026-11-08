#!/usr/bin/env bash
# Back up a SQLite or PostgreSQL database to a timestamped file and keep only
# the newest N backups.
#
# Usage: db_backup.sh --type sqlite   --database FILE      --dest-dir DIR [--keep N]
#        db_backup.sh --type postgres --database NAME [--host H] [--port P] [--user U]
#                                                         --dest-dir DIR [--keep N]
#
# PostgreSQL: the password is read from the PGPASSWORD environment variable
# (ScriptHub passes it that way; it never appears on the command line).
set -euo pipefail

TYPE="" DB="" DEST="" HOST="" PORT="" USER_NAME="" KEEP=7

die()  { echo "ERROR: $*" >&2; exit 2; }
need() { [ $# -ge 2 ] || die "option $1 needs a value"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --type)     need "$@"; TYPE="$2"; shift 2 ;;
    --database) need "$@"; DB="$2"; shift 2 ;;
    --dest-dir) need "$@"; DEST="$2"; shift 2 ;;
    --host)     need "$@"; HOST="$2"; shift 2 ;;
    --port)     need "$@"; PORT="$2"; shift 2 ;;
    --user)     need "$@"; USER_NAME="$2"; shift 2 ;;
    --keep)     need "$@"; KEEP="$2"; shift 2 ;;
    -h|--help)  sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ -n "$TYPE" ] && [ -n "$DB" ] && [ -n "$DEST" ] || die "--type, --database and --dest-dir are required"
case "$KEEP" in ''|*[!0-9]*) die "--keep must be a number" ;; esac
[ "$KEEP" -ge 1 ] || die "--keep must be at least 1"
case "$HOST"      in -*|*[!A-Za-z0-9._-]*) die "invalid host: $HOST" ;; esac
case "$PORT"      in *[!0-9]*) die "invalid port: $PORT" ;; esac
case "$USER_NAME" in -*|*[!A-Za-z0-9._@-]*) die "invalid user name: $USER_NAME" ;; esac

mkdir -p "$DEST"
stamp="$(date +%Y%m%d-%H%M%S)"

case "$TYPE" in
  sqlite)
    [ -f "$DB" ] || die "SQLite database not found: $DB"
    base="$(basename "$DB")"
    out="$DEST/${base%.*}-$stamp.sqlite3"
    echo "Backing up $DB -> $out"
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "$DB" ".backup '$out'"          # consistent even while the app is writing
    else
      echo "(sqlite3 not installed: using a plain file copy; stop the app for a guaranteed-consistent copy)"
      cp -- "$DB" "$out"
    fi
    pattern="${base%.*}-*.sqlite3" ;;
  postgres)
    case "$DB" in -*|*[!A-Za-z0-9._-]*) die "invalid database name: $DB" ;; esac
    command -v pg_dump >/dev/null 2>&1 || die "pg_dump is not installed"
    out="$DEST/$DB-$stamp.dump"
    args=(--format=custom --no-password --file "$out")
    [ -z "$HOST" ] || args+=(--host "$HOST")
    [ -z "$PORT" ] || args+=(--port "$PORT")
    [ -z "$USER_NAME" ] || args+=(--username "$USER_NAME")
    echo "Backing up database $DB -> $out"
    pg_dump "${args[@]}" "$DB"
    pattern="$DB-*.dump" ;;
  *) die "unknown database type: $TYPE (use sqlite or postgres)" ;;
esac

size="$(wc -c < "$out" | tr -d ' ')"
[ "$size" -gt 0 ] || { rm -f -- "$out"; die "the backup is empty; it was removed"; }
echo "Created $out ($size bytes)"

# Retention: keep the newest $KEEP, delete the rest.
removed=0
while IFS= read -r old; do
  [ -n "$old" ] || continue
  rm -f -- "$old"; echo "Removed old backup: $old"; removed=$((removed + 1))
done < <(ls -1t "$DEST"/$pattern 2>/dev/null | tail -n +"$((KEEP + 1))")
echo "Backups kept: $(ls -1 "$DEST"/$pattern 2>/dev/null | wc -l | tr -d ' ') (removed $removed)"
