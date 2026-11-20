#!/bin/bash
set -euo pipefail
umask 077
mode=${1:---publish}
case "$mode" in
  --dry-run|--publish) ;;
  *) exit 2 ;;
esac

cd /opt/daily-papers
reader=$(/usr/bin/python3 -c 'import json; print(json.load(open("settings.json"))["reader_user"])')
candidates=$(mktemp)
trap 'rm -f -- "$candidates"' EXIT

/usr/sbin/runuser --login "$reader" --command   'exec /usr/bin/python3 -B /opt/daily-papers/daily_reader.py'   </dev/null >"$candidates"
/usr/sbin/runuser --user daily-publisher --   /usr/bin/python3 -B /opt/daily-papers/publisher_batch.py "$mode" <"$candidates"
