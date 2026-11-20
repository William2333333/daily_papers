"""Manually archive one reviewed candidate and publish its Discord message."""
import argparse
import fcntl
import getpass
import json
import os
import re
import secrets
import stat
import sys
from functools import partial
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from publisher_preflight import SETTINGS, identifiers, preflight, records

WEBHOOK = re.compile(
    r"https://discord\.com/api/(?:v10/)?webhooks/[0-9]+/[A-Za-z0-9_-]+"
)
DISCORD_HEADERS = {"User-Agent": "DailyPapers/1.0"}
CREDENTIAL_NAMES = ("ZOTERO_USER_ID", "ZOTERO_API_KEY", "DISCORD_WEBHOOK_URL")


def validate_credentials(values):
    user_id, api_key, webhook = values
    if (
        not re.fullmatch(r"[1-9][0-9]*", user_id)
        or not api_key.strip()
        or not WEBHOOK.fullmatch(webhook)
    ):
        raise ValueError("请检查 Zotero 用户 ID、API 密钥和 Discord webhook 的输入格式")
    return values


def load_credentials(path):
    with path.open(encoding="utf-8") as file:
        info = os.fstat(file.fileno())
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("凭据文件必须归发布账号所有，权限必须为 0600")
        data = json.load(file)
    return validate_credentials(tuple(data[name] for name in CREDENTIAL_NAMES))


def initialize_credentials(path):
    values = validate_credentials((
        input("Zotero 用户 ID: ").strip(),
        getpass.getpass("Zotero 写入密钥: ").strip(),
        getpass.getpass("Discord webhook: ").strip(),
    ))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(dict(zip(CREDENTIAL_NAMES, values)), file)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(ProxyHandler({}), NoRedirect())


def post_json(url, payload, headers):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with OPENER.open(request, timeout=20) as response:
        return json.load(response)


def get_json(url, headers=None):
    request = Request(url, headers=headers or {})
    with OPENER.open(request, timeout=20) as response:
        return json.load(response)


def publish(candidate, settings, api, api_key, webhook):
    preview = preflight(candidate, settings, partial(records, api=api, api_key=api_key))
    if preview["duplicates"]:
        return {"status": "duplicate", "duplicates": preview["duplicates"]}

    archive = preview["proposed_archive"]
    keys = dict(sorted(identifiers(archive)))
    item = {
        "itemType": "journalArticle" if "DOI" in keys and not keys["DOI"].startswith("10.48550/arxiv.") else "preprint",
        "title": archive["title"],
        "collections": [preview["collection"]["key"]],
    }
    if archive.get("url"):
        item["url"] = archive["url"]
    if "DOI" in keys:
        item["DOI"] = keys["DOI"]
    arxiv = sorted(value for kind, value in identifiers(archive) if kind == "arXiv")
    if arxiv:
        item["extra"] = "\n".join(f"arXiv: {value}" for value in arxiv)

    try:
        response = post_json(f"{api}/items", [item], {
            "Zotero-API-Version": "3",
            "Zotero-API-Key": api_key,
            "Zotero-Write-Token": secrets.token_hex(16),
        })
        item_key = response["successful"]["0"]["key"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        report_failure("Zotero 写入未确认，请先查库确认结果", error)
        return {"status": "archive_unconfirmed"}

    return send_discord(candidate["discord_message"], webhook, item_key)


def resume_discord(candidate, settings, api, api_key, webhook, item_key, channel_id):
    item = get_json(f"{api}/items/{item_key}", {
        "Zotero-API-Version": "3",
        "Zotero-API-Key": api_key,
    })["data"]
    if (
        item.get("deleted")
        or settings["zotero_collection_key"] not in item.get("collections", [])
        or not identifiers(candidate) & identifiers(item)
    ):
        raise ValueError("条目未匹配候选或目标分类")
    if get_json(webhook, DISCORD_HEADERS).get("channel_id") != channel_id:
        raise ValueError("Webhook 不属于指定频道")
    return send_discord(candidate["discord_message"], webhook, item_key)


def send_discord(message, webhook, item_key):
    try:
        response = post_json(f"{webhook}?wait=true", {
            "content": message,
            "allowed_mentions": {"parse": []},
        }, DISCORD_HEADERS)
        message_id = response["id"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        report_failure("Zotero 已归档，Discord 发送未确认，请先检查目标频道", error)
        return {"status": "discord_unconfirmed", "zotero_item_key": item_key}

    return {"status": "published", "zotero_item_key": item_key, "discord_message_id": message_id}


def report_failure(stage, error):
    detail = f"HTTP {error.code}" if isinstance(error, HTTPError) else type(error).__name__
    print(f"{stage}（{detail}）。", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path, nargs="?")
    parser.add_argument("--credentials-file", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--publish", action="store_true")
    mode.add_argument("--resume-discord", metavar="ZOTERO_ITEM_KEY")
    mode.add_argument("--init-credentials", action="store_true")
    mode.add_argument("--check-credentials", action="store_true")
    parser.add_argument("--discord-channel-id")
    args = parser.parse_args()
    if args.init_credentials or args.check_credentials:
        path = args.credentials_file or Path.home() / ".config/daily-papers/credentials.json"
        try:
            if args.init_credentials:
                initialize_credentials(path)
            else:
                load_credentials(path)
        except (OSError, ValueError, KeyError, TypeError, EOFError) as error:
            report_failure("凭据文件检查或初始化失败", error)
            return 1
        print("凭据文件已创建。" if args.init_credentials else "凭据文件可读取，所有者、权限和格式均通过本地检查。")
        return 0
    if args.candidate is None:
        parser.error("发布或补发需要候选文件")
    if args.resume_discord and (
        not re.fullmatch(r"[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}", args.resume_discord)
        or not re.fullmatch(r"[0-9]+", args.discord_channel_id or "")
    ):
        parser.error("补发需要有效的 Zotero 条目 ID 和 --discord-channel-id")

    try:
        if args.credentials_file:
            user_id, api_key, webhook = load_credentials(args.credentials_file)
        else:
            user_id, api_key, webhook = validate_credentials(
                tuple(os.environ.get(name, "") for name in CREDENTIAL_NAMES)
            )
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        message = candidate.get("discord_message")
        if not isinstance(message, str) or not message.strip() or len(message.encode("utf-16-le")) > 4000:
            parser.error("候选文件需要有效的 Discord 正文")
        api = f"https://api.zotero.org/users/{user_id}"
        with (Path.home() / ".daily-papers-publish.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.resume_discord:
                result = resume_discord(
                    candidate, settings, api, api_key, webhook,
                    args.resume_discord, args.discord_channel_id,
                )
            else:
                result = publish(candidate, settings, api, api_key, webhook)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        report_failure("发布预检失败", error)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in ("published", "duplicate") else 1


if __name__ == "__main__":
    sys.exit(main())
