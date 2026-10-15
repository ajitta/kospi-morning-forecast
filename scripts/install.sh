#!/usr/bin/env bash
# Install the KOSPI morning forecast skill + scripts into Hermes and (optionally) schedule it.
#
#   bash scripts/install.sh                 # install skill + scripts only
#   bash scripts/install.sh --cron          # also create the weekday 08:00 KST cron job (delivers to home channel)
#   bash scripts/install.sh --cron telegram # pick a delivery target (origin|local|telegram|discord|platform:chat_id)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILL_DIR="$HERMES_HOME/skills/research/kospi-morning-forecast"

command -v hermes >/dev/null || { echo "hermes CLI not found — install Hermes Agent first"; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }

mkdir -p "$SKILL_DIR" "$HERMES_HOME/scripts" "$HERMES_HOME/data/kospi_forecast"
cp -r "$SRC/SKILL.md" "$SRC/references" "$SKILL_DIR/"
mkdir -p "$SKILL_DIR/scripts"
cp "$SRC"/scripts/*.py "$SRC/scripts/install.sh" "$SKILL_DIR/scripts/"
cp "$SRC"/scripts/kospi_morning.py "$SRC"/scripts/kospi_model.py "$SRC"/scripts/kospi_record.py "$HERMES_HOME/scripts/"
echo "✓ skill   → $SKILL_DIR"
echo "✓ scripts → $HERMES_HOME/scripts/kospi_{morning,model,record}.py"

echo "… smoke test (fetches live data, ~5s)"
if KOSPI_FORECAST_DIR="$(mktemp -d)" python3 "$HERMES_HOME/scripts/kospi_morning.py" --force | grep -q "기준 상승확률 P(up)"; then
  echo "✓ data sources reachable, model fitted"
else
  echo "! smoke test did not produce a forecast — run: python3 $HERMES_HOME/scripts/kospi_morning.py --force"
fi

if [[ "${1:-}" == "--cron" ]]; then
  DELIVER="${2:-origin}"
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
    Asia/Seoul|Asia/Tokyo|local:+0900) SCHED="0 8 * * 1-5" ;;
    UTC|Etc/UTC|local:+0000) SCHED="0 23 * * 0-4" ;;
    *) echo "! Hermes timezone is '$TZNAME'. Set it to Asia/Seoul (timezone: Asia/Seoul in config.yaml)"
       echo "  or create the job manually with a schedule equal to 08:00 KST on weekdays."; exit 1 ;;
  esac
  hermes cron create "$SCHED" "$(cat "$SRC/references/cron-prompt.md")" \
    --name "KOSPI 모닝 전망" --skill kospi-morning-forecast --script kospi_morning.py --deliver "$DELIVER"
  echo "✓ cron job created ($SCHED, tz=$TZNAME, deliver=$DELIVER). Check: hermes cron list"
fi
