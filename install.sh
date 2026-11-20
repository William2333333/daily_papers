#!/bin/bash
set -euo pipefail

if (( $# < 3 || $# > 5 )); then
  echo "Usage: sudo bash install.sh READER_USER ZOTERO_COLLECTION_KEY DISCORD_CHANNEL_ID [en|zh-CN|fr|es|ja] [COLLECTION_NAME]" >&2
  exit 2
fi
if (( EUID != 0 )); then
  echo "Run with sudo." >&2
  exit 2
fi

reader=$1
collection=$2
channel=$3
language=${4:-en}
name=${5:-Daily Papers}
case "$language" in
  en|zh-CN|fr|es|ja) ;;
  *) echo "Unsupported language." >&2; exit 2 ;;
esac
if [[ ! $collection =~ ^[A-Z0-9]{8}$ || ! $channel =~ ^[0-9]+$ ]]; then
  echo "Expected an eight-character Zotero collection key and a numeric Discord channel ID." >&2
  exit 2
fi
if ! reader_uid=$(id -u -- "$reader") || [[ $reader_uid == 0 || $reader == daily-publisher ]]; then
  echo "Choose an existing non-root, non-publisher account with Codex configured." >&2
  exit 2
fi
if ! id daily-publisher >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash daily-publisher
  chmod 0700 /home/daily-publisher
fi
if [[ $(id -u daily-publisher) == "$reader_uid" ]]; then
  echo "Reader and publisher must have different UIDs." >&2
  exit 2
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
install -d -o root -g root -m 0755 /opt/daily-papers
install -o root -g root -m 0644   daily_settings.py daily_reader.py publisher.py publisher_preflight.py publisher_batch.py   run_daily_papers.sh /opt/daily-papers/
if [[ ! -e /opt/daily-papers/requirements.md ]]; then
  install -o root -g root -m 0644 requirements.md /opt/daily-papers/
fi
install -o root -g root -m 0644   systemd/daily-papers.service systemd/daily-papers.timer /etc/systemd/system/
python3 - "$reader" "$collection" "$channel" "$language" "$name" <<'PY'
import json
import sys
from pathlib import Path

fields = ("reader_user", "zotero_collection_key", "discord_channel_id", "language", "zotero_collection_name")
path = Path("/opt/daily-papers/settings.json")
settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
settings.update(dict(zip(fields, sys.argv[1:])))
settings.setdefault("model_provider", "codex")
settings.setdefault("model", "")
settings.setdefault("requirements_file", "requirements.md")
path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
path.chmod(0o644)
PY
systemctl daemon-reload
echo "Installed. Configure publisher credentials and run a dry run before enabling the timer."
