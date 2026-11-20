#!/bin/bash
set -euo pipefail
umask 077
cd /opt/daily-papers
read_setting() {
  /usr/bin/python3 -c 'import json,sys; print(json.load(open("settings.json")).get(sys.argv[1], sys.argv[2]))' "$1" "$2"
}
mode=${1:---scheduled}
case "$mode" in
  --scheduled)
    case "$(read_setting publication_mode auto)" in
      auto) mode=--publish ;;
      review) mode=--dry-run ;;
      *) exit 2 ;;
    esac ;;
  --dry-run|--publish) ;;
  *) exit 2 ;;
esac
reader=$(read_setting reader_user '')
batch() {
  /usr/sbin/runuser --user daily-publisher -- /usr/bin/python3 -B /opt/daily-papers/publisher_batch.py "$@"
}
candidates=$(mktemp)
trap 'rm -f -- "$candidates"' EXIT
if ! /usr/sbin/runuser --login "$reader" --command 'exec /usr/bin/python3 -B /opt/daily-papers/daily_reader.py' </dev/null >"$candidates"; then
  batch --record-reader-failure </dev/null
  exit 1
fi
batch "$mode" <"$candidates"
