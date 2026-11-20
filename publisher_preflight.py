"""Read-only Zotero preflight and Discord preview."""
import argparse
import errno
import json
import os
import re
import sys
import unicodedata
from functools import partial
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

API = "http://127.0.0.1:23119/api/users/0"
LOCAL_OPENER = build_opener(ProxyHandler({}))
SETTINGS = Path(__file__).with_name("settings.json")
DOI_PREFIX = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", re.I)
ARXIV_ID = r"(?:[a-z][a-z.-]*/\d{7}|\d{4}\.\d{4,5})"
ARXIV = re.compile(
    rf"(?:arxiv\s*[:：#]?\s*|arxiv\.org/(?:abs|pdf|html)/|10\.48550/arxiv\.)"
    rf"({ARXIV_ID})(?:v\d+)?(?=$|\.pdf\b|\.(?!\d)|[?/#\s\]\)>,;])", re.I
)
BARE_ARXIV = re.compile(
    rf"(?<![\w./-])({ARXIV_ID})(?:v\d+)?(?:\.pdf)?(?=$|\.(?!\d)|[\s\]\)>,;])", re.I
)


def arxiv_ids(value, *, explicit=False):
    value = unicodedata.normalize("NFKC", value or "").strip()
    matches = {match.group(1).casefold() for match in ARXIV.finditer(value)}
    if explicit and not matches and "://" not in value:
        matches = {match.group(1).casefold() for match in BARE_ARXIV.finditer(value)}
    return matches


def get_page(resource, start, limit, *, api, api_key):
    request = Request(
        f"{api}/{resource}?start={start}&limit={limit}",
        headers={"Zotero-API-Version": "3"},
    )
    if api_key:
        request.add_unredirected_header("Zotero-API-Key", api_key)
    with LOCAL_OPENER.open(request, timeout=15) as response:
        return json.load(response), response.headers.get("Total-Results")


def records(resource, *, api=API, api_key=None):
    start = 0
    while True:
        rows, total = get_page(resource, start, 100, api=api, api_key=api_key)
        if not rows:
            return
        yield from rows
        start += len(rows)
        if total is not None and start >= int(total):
            return


def identifiers(item):
    doi = DOI_PREFIX.sub("", (item.get("DOI") or "").strip()).casefold()
    title = unicodedata.normalize("NFKC", (item.get("title") or "")).casefold()
    title = " ".join("".join(c if c.isalnum() else " " for c in title).split())
    arxiv = arxiv_ids(item.get("arxiv_id"), explicit=True)
    for field in ("DOI", "url", "extra", "archiveID"):
        arxiv.update(arxiv_ids(item.get(field), explicit=field == "archiveID"))
    keys = {("arXiv", value) for value in arxiv}
    if doi:
        keys.add(("DOI", doi))
    if title:
        keys.add(("标题", title))
    return keys


def preflight(candidate, settings, read_records=records):
    if not candidate.get("title", "").strip():
        raise ValueError("候选必须包含 title")
    keys = identifiers(candidate)
    collection_key = settings["zotero_collection_key"]
    collection_name = settings["zotero_collection_name"]
    if not any(
        row["key"] == collection_key and row["data"]["name"] == collection_name
        for row in read_records("collections")
    ):
        raise ValueError("Zotero 中的分类名称或 ID 不匹配")

    duplicates = []
    for row in read_records("items/top"):
        matched = keys & identifiers(row["data"])
        if matched:
            duplicates.append({
                "item_key": row["key"],
                "matched_by": sorted({kind for kind, _ in matched}),
            })

    return {
        "collection": {"key": collection_key, "name": collection_name},
        "duplicates": duplicates,
        "proposed_archive": None if duplicates else {
            field: candidate[field]
            for field in ("title", "DOI", "arxiv_id", "url")
            if field in candidate
        },
        "discord_preview": None if duplicates else candidate.get("discord_message"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path, help="JSON object with title, optional DOI, arxiv_id, url and discord_message")
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--web-api", action="store_true", help="Read the synced personal Zotero library")
    args = parser.parse_args()
    read_records = records
    target = "Zotero 本地 API（127.0.0.1:23119）"
    if args.web_api:
        user_id = os.environ.get("ZOTERO_USER_ID", "")
        api_key = os.environ.get("ZOTERO_API_KEY", "")
        if not re.fullmatch(r"[1-9][0-9]*", user_id) or not api_key.strip():
            parser.error("Web API 模式需要在发布进程中配置数字 ZOTERO_USER_ID 和 ZOTERO_API_KEY")
        read_records = partial(records, api=f"https://api.zotero.org/users/{user_id}", api_key=api_key)
        target = "Zotero Web API（api.zotero.org）"
    try:
        result = preflight(
            json.loads(args.candidate.read_text()), json.loads(SETTINGS.read_text()), read_records
        )
    except HTTPError as error:
        hint = "请检查用户 ID、密钥的读取权限以及云端同步。" if args.web_api else "请检查本地 API 是否已启用。"
        print(f"{target}返回 HTTP {error.code}。{hint}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError) as error:
        reason = error.reason if isinstance(error, URLError) else error
        detail = {
            errno.ECONNREFUSED: "连接被拒绝，请确认 Zotero Desktop 与脚本在同一台主机上运行。",
            errno.EACCES: "当前执行环境拒绝访问本地端口。",
            errno.EPERM: "当前执行环境拒绝访问本地端口。",
        }.get(getattr(reason, "errno", None), "请检查 Zotero Desktop 是否运行以及本地 API 是否可用。")
        if isinstance(reason, TimeoutError):
            detail = "连接或响应超时，请检查 Zotero Desktop 是否正常响应。"
        if args.web_api:
            detail = "请检查发布进程能否通过 HTTPS 访问 api.zotero.org。"
        print(f"无法访问{target}。{detail}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"预检失败，请检查候选 JSON、分类配置和 Zotero 响应（{type(error).__name__}）。", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
