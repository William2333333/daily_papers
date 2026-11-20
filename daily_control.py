"""Administrator entry points for the setup and operations interface."""
import argparse
import json
import os
import pwd
import subprocess
from pathlib import Path

INSTALL = Path("/opt/daily-papers")
PYTHON = "/usr/bin/python3"
RUNUSER = "/usr/sbin/runuser"
SYSTEMCTL = "/usr/bin/systemctl"
PUBLISHER = "daily-publisher"


def batch(*arguments, **kwargs):
    return subprocess.run([
        RUNUSER, "--user", PUBLISHER, "--", PYTHON, "-B",
        str(INSTALL / "publisher_batch.py"), *arguments,
    ], capture_output=True, text=True, **kwargs)


def properties(unit, names):
    command = [SYSTEMCTL, "show", unit, *(f"--property={name}" for name in names)]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def health(settings):
    reader = settings["reader_user"]
    key_path = Path(pwd.getpwnam(PUBLISHER).pw_dir) / ".config/daily-papers/credentials.json"
    # Verify the file exists first so a missing file cannot pass the isolation check.
    isolation = subprocess.run([
        RUNUSER, "--user", reader, "--", PYTHON, "-c",
        "import os, sys;\ntry: fd = os.open(sys.argv[1], os.O_RDONLY)\n"
        "except PermissionError: sys.exit(0)\nelse: os.close(fd); sys.exit(3)",
        str(key_path),
    ], stdin=subprocess.DEVNULL, capture_output=True)
    isolated = key_path.is_file() and isolation.returncode == 0
    publisher = batch("--check-connections", stdin=subprocess.DEVNULL)
    try:
        results = json.loads(publisher.stdout)
    except ValueError:
        results = [{"name": name, "ok": False, "detail": "Publisher check failed"} for name in ("Zotero", "Discord")]
    results.append({"name": "Credential isolation", "ok": isolated,
                    "detail": "Reader cannot access the credential path" if isolated else "Reader can access the path or credential file is missing"})
    try:
        model = subprocess.run([
            RUNUSER, "--login", reader, "--command",
            f"exec {PYTHON} -B /opt/daily-papers/daily_reader.py --check-provider",
        ], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=330)
        model_ok = model.returncode == 0
        model_detail = "Check the reader account, selected model, credentials and network access"
    except subprocess.TimeoutExpired:
        model_ok = False
        model_detail = "Model check timed out"
    results.append({"name": "Model", "ok": model_ok, "detail": "" if model_ok else model_detail})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("health", "status", "approve", "retry"))
    parser.add_argument("item_key", nargs="?")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Administrator privileges required")
    settings = json.loads((INSTALL / "settings.json").read_text(encoding="utf-8"))
    if args.action == "health":
        result = health(settings)
    elif args.action == "status":
        state = json.loads(batch("--state", stdin=subprocess.DEVNULL, check=True).stdout)
        result = {
            "timer": properties("daily-papers.timer", ("ActiveState", "NextElapseUSecRealtime", "LastTriggerUSec")),
            "service": properties("daily-papers.service", ("ActiveState", "Result")),
            "mode": settings.get("publication_mode", "auto"),
            "time": settings.get("publish_time"), "timezone": settings.get("timezone"), **state,
        }
    else:
        import sys

        if args.action == "approve":
            outcome = batch("--approve", input=sys.stdin.read())
        else:
            if not args.item_key:
                parser.error("An item key is required for retry")
            outcome = batch("--retry", args.item_key, stdin=subprocess.DEVNULL)
        if outcome.stderr:
            print(outcome.stderr, file=sys.stderr, end="")
        print(outcome.stdout, end="")
        return outcome.returncode
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
