#!/usr/bin/env bash
# Install the KOSPI morning forecast skill + scripts into Hermes and (optionally) schedule it.
#
#   bash scripts/install.sh                 # install skill + scripts only
#   bash scripts/install.sh --cron          # also create the weekday 08:00 KST briefing job + the after-close snapshot job
#   bash scripts/install.sh --cron telegram # pick a delivery target (origin|local|telegram|discord|platform:chat_id)
#   bash scripts/install.sh --snapshot-cron # upgrading from <=1.1: add only the after-close snapshot job
#   bash scripts/install.sh --with-history  # also seed the forecast log from the repo's `data` branch
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILL_DIR="$HERMES_HOME/skills/research/kospi-morning-forecast"
# must match the scripts' default (they don't follow HERMES_HOME)
DATA_DIR="${KOSPI_FORECAST_DIR:-$HOME/.hermes/data/kospi_forecast}"
DATA_REPO="${KOSPI_DATA_REPO:-https://github.com/ajitta/kospi-morning-forecast.git}"

CRON=0; SNAPCRON=0; DELIVER=origin; HISTORY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cron) CRON=1; SNAPCRON=1; if [[ $# -gt 1 && "$2" != --* ]]; then DELIVER="$2"; shift; fi ;;
    --snapshot-cron) SNAPCRON=1; if [[ $# -gt 1 && "$2" != --* ]]; then DELIVER="$2"; shift; fi ;;
    --with-history) HISTORY=1 ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
  shift
done

command -v hermes >/dev/null || { echo "hermes CLI not found — install Hermes Agent first"; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }

mkdir -p "$SKILL_DIR" "$HERMES_HOME/scripts" "$DATA_DIR"
cp -r "$SRC/SKILL.md" "$SRC/references" "$SKILL_DIR/"
mkdir -p "$SKILL_DIR/scripts"
cp "$SRC"/scripts/*.py "$SRC/scripts/install.sh" "$SKILL_DIR/scripts/"
cp "$SRC"/scripts/kospi_morning.py "$SRC"/scripts/kospi_model.py "$SRC"/scripts/kospi_record.py \
   "$SRC"/scripts/kospi_snapshot.py "$HERMES_HOME/scripts/"
echo "✓ skill   → $SKILL_DIR"
echo "✓ scripts → $HERMES_HOME/scripts/kospi_{morning,model,record,snapshot}.py"

echo "… smoke test (fetches live data, ~5s)"
if KOSPI_FORECAST_DIR="$(mktemp -d)" python3 "$HERMES_HOME/scripts/kospi_morning.py" --force | grep -q "기준 상승확률 P(up)"; then
  echo "✓ data sources reachable, model fitted"
else
  echo "! smoke test did not produce a forecast — run: python3 $HERMES_HOME/scripts/kospi_morning.py --force"
fi

if [[ $HISTORY == 1 ]]; then
  # The data branch is the publisher's track record (forecasts + realised closes). Your own forecasts
  # append to it locally; they are committed but never pushed unless you set `git config kospi.push true`.
  if [[ -d "$DATA_DIR/.git" ]]; then
    git -C "$DATA_DIR" pull -q --ff-only && echo "✓ history updated → $DATA_DIR" \
      || echo "! $DATA_DIR has diverged from the data branch — left as is"
  elif [[ -n "$(ls -A "$DATA_DIR")" ]]; then
    echo "! $DATA_DIR already has data — not overwritten. Move it aside and re-run to seed history."
  else
    git clone -q --branch data --single-branch "$DATA_REPO" "$DATA_DIR"
    echo "✓ history → $DATA_DIR ($(wc -l < "$DATA_DIR/predictions.jsonl") forecasts)"
  fi
fi

if [[ $CRON == 1 || $SNAPCRON == 1 ]]; then
  # Cron schedules use Hermes' timezone (HERMES_TIMEZONE / `timezone:` in config.yaml / server local).
  TZNAME="$(python3 - <<'EOF'
import os, re
tz = os.environ.get("HERMES_TIMEZONE", "")
cfg = os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), "config.yaml")
if not tz and os.path.exists(cfg):
    m = re.search(r'^timezone:\s*["\']?([^"\'\s]+)', open(cfg, encoding="utf-8").read(), re.M)
    tz = m.group(1) if m else ""
if not tz:
    import time
    tz = "local:" + time.strftime("%z")
print(tz)
EOF
)"
  case "$TZNAME" in
    Asia/Seoul|Asia/Tokyo|local:+0900) SCHED="0 8 * * 1-5"; SNAP_SCHED="10 16,20 * * 1-5" ;;
    UTC|Etc/UTC|local:+0000) SCHED="0 23 * * 0-4"; SNAP_SCHED="10 7,11 * * 1-5" ;;
    *) echo "! Hermes timezone is '$TZNAME'. Set it to Asia/Seoul (timezone: Asia/Seoul in config.yaml)"
       echo "  or create the job manually with a schedule equal to 08:00 KST on weekdays."; exit 1 ;;
  esac
  if [[ $CRON == 1 ]]; then
    hermes cron create "$SCHED" "$(cat "$SRC/references/cron-prompt.md")" \
      --name "KOSPI 모닝 전망" --skill kospi-morning-forecast --script kospi_morning.py --deliver "$DELIVER"
    echo "✓ briefing job created ($SCHED, tz=$TZNAME, deliver=$DELIVER)"
  fi
  # Program / breadth / sector / theme data is wiped by the portals before the next open, so a
  # script-only job saves it after the close (16:10 KST, retried 20:10). Silent unless incomplete.
  if hermes cron list 2>/dev/null | grep -q "KOSPI 장마감 스냅샷"; then
    echo "✓ snapshot job already exists"
  else
    hermes cron create "$SNAP_SCHED" --no-agent --script kospi_snapshot.py \
      --name "KOSPI 장마감 스냅샷" --deliver "$DELIVER"
    echo "✓ snapshot job created ($SNAP_SCHED, tz=$TZNAME, deliver=$DELIVER for warnings only)"
  fi
  echo "  Check: hermes cron list"
fi
