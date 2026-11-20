"""Private persistent publication state. Callers hold the publisher lock."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PublisherState:
    def __init__(self, path=None):
        self.path = path or Path.home() / ".local/state/daily-papers/state.json"
        self.data = {"review": None, "deliveries": {}, "runs": []}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.path.parent, mode="w", encoding="utf-8", delete=False) as file:
            temp = Path(file.name)
            try:
                json.dump(self.data, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
                file.close()
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)

    def review(self, entries, scope):
        self.data["review"] = {
            "id": uuid4().hex, "at": now(), "entries": entries, "scope": scope,
        }
        self.save()
        return self.data["review"]["id"]

    def queue(self, candidate, item_key, scope):
        self.data["deliveries"][item_key] = {
            "candidate": candidate, "scope": scope, "status": "discord_unconfirmed",
            "zotero_item_key": item_key, "at": now(),
        }
        self.save()

    def delivery_result(self, result):
        key = result["zotero_item_key"]
        self.data["deliveries"][key].update(result, at=now())
        self.save()

    def record_run(self, mode, results, failed=False):
        counts = {}
        for result in results:
            status = result["status"]
            counts[status] = counts.get(status, 0) + 1
        self.data["runs"].append({"at": now(), "mode": mode, "failed": failed, "counts": counts})
        self.data["runs"] = self.data["runs"][-30:]
        self.save()
