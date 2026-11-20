"""Read a Daily Papers candidate array from stdin and preview or publish it."""
import argparse
import fcntl
import json
import sys
from functools import partial
from pathlib import Path

from daily_settings import paper_limit
from publisher import DISCORD_HEADERS, SETTINGS, get_json, load_credentials, publish, report_failure
from publisher_preflight import arxiv_ids, identifiers, preflight, records


def read_candidates(limit):
    candidates = json.load(sys.stdin)
    if not isinstance(candidates, list) or len(candidates) > limit:
        raise ValueError(f"Expected an array with at most {limit} candidates")
    return candidates


def prepare_candidates(candidates):
    seen = set()
    for index, candidate in enumerate(candidates, start=1):
        try:
            candidate = prepare_candidate(candidate)
            keys = identifiers(candidate)
            if seen & keys:
                yield index, None, {"status": "duplicate_in_batch"}
                continue
            seen.update(keys)
            yield index, candidate, {}
        except (ValueError, TypeError) as error:
            yield index, None, {"status": "invalid_candidate", "reason": str(error)}


def prepare_candidate(candidate):
    if not isinstance(candidate, dict):
        raise ValueError("Expected a JSON object")
    fields = ("title", "DOI", "arxiv_id", "url", "discord_message")
    if not all(candidate.get(field) is None or isinstance(candidate[field], str) for field in fields):
        raise ValueError("Candidate fields must be strings or null")
    clean = {field: (candidate.get(field) or "").strip() for field in fields}
    message = clean["discord_message"]
    if not any(kind == "标题" for kind, _ in identifiers(clean)) or not message:
        raise ValueError("Title and Discord message are required")
    if len(message.encode("utf-16-le")) > 4000:
        raise ValueError("Discord message is too long")
    ids = {value for kind, value in identifiers(clean) if kind == "arXiv"}
    if len(ids) > 1:
        raise ValueError("Conflicting arXiv IDs")
    if clean["arxiv_id"] and not arxiv_ids(clean["arxiv_id"], explicit=True):
        if not ids and not any(kind == "DOI" for kind, _ in identifiers(clean)):
            raise ValueError("Unrecognized arXiv ID without a DOI or recoverable arXiv link")
    clean["arxiv_id"] = next(iter(ids), "")
    return clean


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--publish", action="store_true")
    parser.add_argument(
        "--credentials-file", type=Path,
        default=Path.home() / ".config/daily-papers/credentials.json",
    )
    args = parser.parse_args()
    results = []
    try:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        entries = list(prepare_candidates(read_candidates(paper_limit(settings))))
        if not any(candidate is not None for _, candidate, _ in entries):
            results = [{"index": index, **status} for index, _, status in entries]
        else:
            user_id, api_key, webhook = load_credentials(args.credentials_file)
            api = f"https://api.zotero.org/users/{user_id}"
            if get_json(webhook, DISCORD_HEADERS).get("channel_id") != settings["discord_channel_id"]:
                raise ValueError("Discord webhook channel does not match settings.json")
            read_records = partial(records, api=api, api_key=api_key)
            with (Path.home() / ".daily-papers-publish.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                for index, candidate, status in entries:
                    if candidate is None:
                        results.append({"index": index, **status})
                        continue
                    if args.dry_run:
                        results.append({"index": index, **preflight(candidate, settings, read_records)})
                    else:
                        result = publish(candidate, settings, api, api_key, webhook)
                        results.append({"index": index, **result})
                        if result["status"] not in ("published", "duplicate"):
                            print(json.dumps(results, ensure_ascii=False, indent=2))
                            return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        if results:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        report_failure("每日交接失败", error)
        return 1
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
