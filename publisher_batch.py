"""Read a Daily Papers candidate array from stdin and preview or publish it."""
import argparse
import fcntl
import json
import sys
from urllib.error import HTTPError
from functools import partial
from pathlib import Path

from daily_settings import paper_limit
from publisher import DISCORD_HEADERS, SETTINGS, get_json, load_credentials, publish, report_failure, resume_discord
from publisher_state import PublisherState
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


def publisher_context(settings, credentials_file):
    user_id, api_key, webhook = load_credentials(credentials_file)
    collection = settings["zotero_collection_key"]
    channel = settings["discord_channel_id"]
    scope = {"user_id": user_id, "collection": collection, "channel": channel}
    return f"https://api.zotero.org/users/{user_id}", api_key, webhook, scope


def verification(label, action):
    try:
        action()
    except HTTPError as error:
        return {"name": label, "ok": False, "detail": f"HTTP {error.code}"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {"name": label, "ok": False, "detail": str(error) if type(error) is ValueError else type(error).__name__}
    return {"name": label, "ok": True}


def check_channel(webhook, expected):
    if get_json(webhook, DISCORD_HEADERS).get("channel_id") != expected:
        raise ValueError("Discord webhook channel does not match settings")


def check_zotero(api, api_key, settings, scope):
    headers = {"Zotero-API-Version": "3", "Zotero-API-Key": api_key}
    key = get_json("https://api.zotero.org/keys/current", headers)
    access = key.get("access", {}).get("user", {})
    if str(key.get("userID")) != scope["user_id"] or not (access.get("library") and access.get("write")):
        raise ValueError("Zotero key must match the user and allow personal-library reads and writes")
    collection = get_json(f"{api}/collections/{scope['collection']}", headers)
    if collection["data"]["name"] != settings["zotero_collection_name"]:
        raise ValueError("Zotero collection name does not match settings")


def checks(settings, credentials_file):
    try:
        api, api_key, webhook, scope = publisher_context(settings, credentials_file)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [{"name": name, "ok": False, "detail": "Publisher credentials or destination settings unavailable"}
                for name in ("Zotero", "Discord")]
    return [
        verification("Zotero", lambda: check_zotero(api, api_key, settings, scope)),
        verification("Discord", lambda: check_channel(webhook, scope["channel"])),
    ]


def process(entries, mode, settings, context, state):
    api, api_key, webhook, scope = context
    read_records = partial(records, api=api, api_key=api_key)
    results = []
    review_entries = []
    failed = False
    try:
        if mode != "review" or any(candidate is not None for _, candidate, _ in entries):
            check_channel(webhook, scope["channel"])
        for index, candidate, status in entries:
            result = {"index": index, **status}
            if candidate is not None:
                if mode == "review":
                    preview = preflight(candidate, settings, read_records)
                    result.update(preview, status="duplicate" if preview["duplicates"] else "ready")
                    review_entries.append({"candidate": candidate, **result})
                else:
                    outcome = publish(candidate, settings, api, api_key, webhook,
                                      on_archive=lambda saved, key: state.queue(saved, key, scope))
                    if "zotero_item_key" in outcome:
                        state.delivery_result(outcome)
                    result.update(outcome, title=candidate["title"])
                    if mode == "approve" and outcome["status"] != "archive_unconfirmed":
                        for entry in state.data["review"]["entries"]:
                            if entry["index"] == index:
                                entry.update(outcome)
                                state.save()
                                break
            else:
                review_entries.append(result)
            results.append(result)
            if result["status"] in ("archive_unconfirmed", "discord_unconfirmed"):
                failed = True
                break
        if mode == "review":
            state.review(review_entries, scope)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        failed = True
        raise
    finally:
        state.record_run(mode, results, failed)
    return results, failed


def approved_entries(state, selection, scope, limit):
    review = state.data["review"]
    if not review or review["id"] != selection.get("review_id") or review["scope"] != scope:
        raise ValueError("Preview changed or destinations no longer match. Refresh the review.")
    indices = selection.get("indices")
    if not isinstance(indices, list) or not indices or len(indices) > limit or not all(type(index) is int for index in indices):
        raise ValueError("Select at least one eligible paper within the configured limit.")
    chosen = set(indices)
    ready = {entry["index"]: entry for entry in review["entries"] if entry["status"] == "ready"}
    if chosen - ready.keys():
        raise ValueError("Selected papers are unavailable for publication.")
    return [(index, ready[index]["candidate"], {}) for index in sorted(chosen)]


def retry_delivery(state, item_key, settings, context):
    api, api_key, webhook, scope = context
    delivery = state.data["deliveries"].get(item_key)
    if not delivery or delivery["status"] != "discord_unconfirmed" or delivery["scope"] != scope:
        raise ValueError("No pending delivery for this item and destination.")
    result = resume_discord(delivery["candidate"], settings, api, api_key, webhook, item_key, scope["channel"])
    state.delivery_result(result)
    state.record_run("discord_retry", [result], result["status"] != "published")
    return result


def emit(result):
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--publish", action="store_true")
    modes.add_argument("--check-connections", action="store_true")
    modes.add_argument("--state", action="store_true")
    modes.add_argument("--approve", action="store_true")
    modes.add_argument("--retry")
    modes.add_argument("--record-reader-failure", action="store_true")
    parser.add_argument("--credentials-file", type=Path, default=Path.home() / ".config/daily-papers/credentials.json")
    args = parser.parse_args()
    try:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        if args.check_connections:
            result = checks(settings, args.credentials_file)
            emit(result)
            return 0 if all(check["ok"] for check in result) else 1

        with (Path.home() / ".daily-papers-publish.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = PublisherState()
            if args.state:
                emit(state.data)
                return 0
            if args.record_reader_failure:
                state.record_run("reader", [], failed=True)
                return 0
            context = publisher_context(settings, args.credentials_file)
            if args.retry:
                result = retry_delivery(state, args.retry, settings, context)
                emit(result)
                return 0 if result["status"] == "published" else 1
            mode = "review" if args.dry_run else "approve" if args.approve else "auto"
            limit = paper_limit(settings)
            if args.approve:
                entries = approved_entries(state, json.load(sys.stdin), context[3], limit)
            else:
                entries = list(prepare_candidates(read_candidates(limit)))
            results, failed = process(entries, mode, settings, context, state)
            emit(results)
            return 1 if failed else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        if type(error) is ValueError:
            print(str(error), file=sys.stderr)
        else:
            report_failure("Daily Papers publication failed", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
