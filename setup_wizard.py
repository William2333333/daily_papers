"""Configure Daily Papers from one local form or an SSH-friendly terminal wizard."""
import argparse
import getpass
import json
import os
import pwd
import queue
import re
import shlex
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from daily_reader import LANGUAGES
from daily_settings import DEFAULT_MAX_PAPERS, DEFAULT_TIME, DEFAULT_TIMEZONE, MAX_PAPERS, paper_limit, schedule
from publisher import CREDENTIAL_NAMES, validate_credentials

SOURCE = Path(__file__).resolve().parent
INSTALL = Path("/opt/daily-papers")
TIMER_DROP_IN = Path("/etc/systemd/system/daily-papers.timer.d/schedule.conf")
DRY_RUN = ("/bin/bash", "/opt/daily-papers/run_daily_papers.sh", "--dry-run")
PROVIDER_KEY = ".config/daily-papers/openrouter.key"
PUBLISHER_CONFIG = ".config/daily-papers/credentials.json"
FIELDS = (
    ("reader_user", "Linux reader account"),
    ("model_provider", "Model backend"),
    ("model", "Model ID (optional for Codex)"),
    ("language", "Review language"),
    ("openrouter_key", "OpenRouter API key (OpenRouter only)"),
    ("zotero_collection_name", "Zotero collection name"),
    ("zotero_collection_key", "Zotero collection key (8 characters)"),
    ("zotero_user_id", "Zotero numeric user ID"),
    ("zotero_api_key", "Zotero API key (personal library read/write)"),
    ("discord_channel_id", "Discord channel ID"),
    ("discord_webhook_url", "Discord webhook URL"),
    ("publish_time", "Daily send time (HH:MM)"),
    ("timezone", "Time zone (IANA name)"),
    ("max_papers", "Maximum papers per run"),
)
SECRET_FIELDS = {"openrouter_key", "zotero_api_key", "discord_webhook_url"}
DEFAULTS = {
    "model_provider": "codex",
    "language": "en",
    "zotero_collection_name": "Daily Papers",
    "publish_time": DEFAULT_TIME,
    "timezone": DEFAULT_TIMEZONE,
    "max_papers": str(DEFAULT_MAX_PAPERS),
}
PRIVATE_WRITER = """
import os, sys, tempfile
from pathlib import Path
os.umask(0o077)
target = Path(sys.argv[1])
target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
    temp = Path(output.name)
    try:
        output.write(sys.stdin.buffer.read())
        output.close()
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
"""


def defaults():
    values = DEFAULTS.copy()
    installed = INSTALL / "settings.json"
    if installed.is_file():
        settings = json.loads(installed.read_text(encoding="utf-8"))
        values.update({field: str(settings[field]) for field, _ in FIELDS
                       if field in settings and field not in SECRET_FIELDS})
    topics = INSTALL / "requirements.md"
    if not topics.is_file():
        topics = SOURCE / "requirements.md"
    values["requirements"] = topics.read_text(encoding="utf-8").strip()
    return values


def validated(values):
    data = {field: str(values.get(field, "")).strip() for field, _ in FIELDS}
    data["requirements"] = values.get("requirements", "").strip()
    reader = data["reader_user"]
    try:
        account = pwd.getpwnam(reader)
    except KeyError:
        raise ValueError("Select an existing Linux reader account.") from None
    if account.pw_uid == 0 or reader == "daily-publisher":
        raise ValueError("Reader must be an independent, non-root account.")
    if data["model_provider"] not in ("codex", "openrouter"):
        raise ValueError("Choose Codex or OpenRouter.")
    if data["model_provider"] == "openrouter" and not data["model"]:
        raise ValueError("OpenRouter requires a model ID.")
    if data["language"] not in LANGUAGES:
        raise ValueError("Select a supported review language.")
    if not re.fullmatch(r"[A-Z0-9]{8}", data["zotero_collection_key"]):
        raise ValueError("Enter the eight-character Zotero collection key.")
    if not re.fullmatch(r"[0-9]+", data["discord_channel_id"]):
        raise ValueError("Enter the numeric Discord channel ID.")
    if not data["zotero_collection_name"] or not data["requirements"]:
        raise ValueError("Collection name and selection requirements are required.")
    if "Replace this line with your research topics" in data["requirements"]:
        raise ValueError("Replace the example line in Research requirements with your own topics.")
    schedule(data)
    if not re.fullmatch(r"[0-9]+", data["max_papers"]):
        raise ValueError(f"Enter a paper limit from 1 to {MAX_PAPERS}.")
    data["max_papers"] = paper_limit({"max_papers": int(data["max_papers"])})
    publisher_values = (data["zotero_user_id"], data["zotero_api_key"], data["discord_webhook_url"])
    if any(publisher_values):
        validate_credentials(publisher_values)
    return data


def apply_settings(data):
    if os.geteuid() != 0:
        raise ValueError("The setup helper requires administrator privileges.")
    for unit in ("daily-papers.timer", "daily-papers.service"):
        state = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
        if state.stdout.strip() in {"active", "activating", "deactivating", "reloading"}:
            raise ValueError("Stop the timer and wait for the publisher service before changing settings.")
    time, zone = schedule(data)
    calendar = f"*-*-* {time}:00 {zone.key}"
    subprocess.run(["systemd-analyze", "calendar", calendar], stdout=subprocess.DEVNULL, check=True)
    subprocess.run([
        "/bin/bash", str(SOURCE / "install.sh"), data["reader_user"],
        data["zotero_collection_key"], data["discord_channel_id"], data["language"],
        data["zotero_collection_name"],
    ], check=True)
    values = (data["zotero_user_id"], data["zotero_api_key"], data["discord_webhook_url"])
    publisher_home = Path(pwd.getpwnam("daily-publisher").pw_dir)
    reader_home = Path(pwd.getpwnam(data["reader_user"]).pw_dir)
    private_files = [("daily-publisher", publisher_home / PUBLISHER_CONFIG,
                      json.dumps(dict(zip(CREDENTIAL_NAMES, values))) if any(values) else "")]
    if data["model_provider"] == "openrouter":
        private_files.append((data["reader_user"], reader_home / PROVIDER_KEY, data["openrouter_key"]))
    for user, destination, content in private_files:
        if not content:
            status = subprocess.run([
                "/usr/sbin/runuser", "--user", user, "--", "/usr/bin/test", "-s", str(destination),
            ])
            if status.returncode:
                raise ValueError(f"No saved credentials for {user}. Enter credentials before saving.")
    for user, destination, content in private_files:
        if not content:
            continue
        subprocess.run([
            "/usr/sbin/runuser", "--user", user, "--", "/usr/bin/python3", "-c",
            PRIVATE_WRITER, str(destination),
        ], input=content, text=True, check=True)
    settings_path = INSTALL / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings.update(model_provider=data["model_provider"], model=data["model"], requirements_file="requirements.md")
    settings.update(publish_time=time, timezone=zone.key, max_papers=paper_limit(data))
    (INSTALL / "requirements.md").write_text(data["requirements"] + "\n", encoding="utf-8")
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    TIMER_DROP_IN.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    TIMER_DROP_IN.write_text(f"[Timer]\nOnCalendar=\nOnCalendar={calendar}\n", encoding="utf-8")
    subprocess.run(["systemctl", "daemon-reload"], check=True)


def privileged(command, **kwargs):
    if os.geteuid() != 0:
        command = ["sudo", "-n", "--", *command]
    return subprocess.run(command, check=True, **kwargs)


def save(values):
    data = validated(values)
    if os.geteuid() == 0:
        apply_settings(data)
        return
    privileged([
        "/usr/bin/python3", "-B", str(Path(__file__).resolve()), "--apply",
    ], input=json.dumps(data, ensure_ascii=False), text=True)


def terminal(values):
    print("Daily Papers setup. Press Enter to keep a value shown in brackets.")
    print("To reuse publisher credentials, leave Zotero user ID, Zotero API key and webhook blank.")
    for field, label in FIELDS:
        if field == "openrouter_key" and values.get("model_provider") != "openrouter":
            continue
        default = values.get(field, "")
        if field in SECRET_FIELDS:
            values[field] = getpass.getpass(f"{label}: ")
        else:
            suffix = f" [{default}]" if default else ""
            values[field] = input(f"{label}{suffix}: ").strip() or default
    if input("Edit research requirements? [Y/n]: ").strip().lower() not in ("n", "no"):
        editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano")
        with tempfile.TemporaryDirectory(prefix="daily-papers-setup-") as directory:
            file = Path(directory) / "requirements.md"
            file.write_text(values["requirements"] + "\n", encoding="utf-8")
            subprocess.run([*editor, str(file)], check=True)
            values["requirements"] = file.read_text(encoding="utf-8")
    validated(values)
    if input("Save setup and update any credentials entered above? [y/N]: ").lower().strip() == "y":
        save(values)
        print("Saved.")
        if input("Run a read-only test now? [y/N]: ").strip().lower() == "y":
            privileged(DRY_RUN)
            if input("Enable recurring Zotero archiving and Discord posting? [y/N]: ").strip().lower() == "y":
                privileged(("/usr/bin/systemctl", "enable", "--now", "daily-papers.timer"))
        else:
            print("Run a read-only test before enabling the timer.")


def gui(values):
    import tkinter as tk
    import webbrowser
    from tkinter import messagebox, ttk

    try:
        window = tk.Tk()
    except tk.TclError:
        raise ValueError("No usable graphical display. Run setup_wizard.py --terminal.") from None
    window.title("Daily Papers · Setup")
    window.minsize(690, 570)
    window.geometry("800x650")
    window.configure(background="#eef2f8")
    style = ttk.Style(window)
    style.theme_use("clam")
    style.configure("TFrame", background="#ffffff")
    style.configure("TLabel", background="#ffffff", foreground="#1a2940")
    style.configure("Hint.TLabel", foreground="#42546d")
    style.configure("TNotebook", background="#eef2f8", borderwidth=0)
    style.configure("TNotebook.Tab", padding=(18, 9), foreground="#334760")
    style.map("TNotebook.Tab", background=[("selected", "#ffffff")])
    style.configure("TButton", padding=(12, 7))
    style.configure("Primary.TButton", background="#255ac1", foreground="white")
    style.map("Primary.TButton", background=[("active", "#174797"), ("disabled", "#687e9f")])

    banner = tk.Frame(window, background="#172c49", padx=24, pady=17)
    banner.pack(fill="x")
    tk.Label(banner, text="Daily Papers", background="#172c49", foreground="white",
             font=("TkDefaultFont", 20, "bold")).pack(anchor="w")
    tk.Label(banner, text="Research alerts from your topics to Zotero and Discord",
             background="#172c49", foreground="#dce8fa").pack(anchor="w")

    notebook = ttk.Notebook(window)
    notebook.pack(fill="both", expand=True, padx=18, pady=(16, 10))
    entries = {}
    input_widgets = []
    labels = dict(FIELDS)

    def tab(title, hint):
        page = ttk.Frame(notebook, padding=18)
        page.columnconfigure(1, weight=1)
        notebook.add(page, text=title)
        ttk.Label(page, text=hint, style="Hint.TLabel", wraplength=610).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12),
        )
        return page

    def field(page, row, name, options=(), editable=False):
        ttk.Label(page, text=labels[name]).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=5)
        variable = tk.StringVar(value=values.get(name, ""))
        if options:
            widget = ttk.Combobox(page, textvariable=variable, values=options,
                                  state="normal" if editable else "readonly")
        else:
            widget = ttk.Entry(page, textvariable=variable, show="*" if name in SECRET_FIELDS else "")
        widget.grid(row=row, column=1, sticky="ew", pady=5)
        entries[name] = variable
        input_widgets.append(widget)

    research = tab("Research", "Describe the topics you want to follow and the papers you want to exclude.")
    field(research, 1, "language", tuple(LANGUAGES))
    field(research, 2, "max_papers", tuple(str(n) for n in range(1, MAX_PAPERS + 1)))
    ttk.Label(research, text="Research requirements").grid(row=3, column=0, sticky="nw", pady=5)
    editor = ttk.Frame(research)
    editor.grid(row=3, column=1, sticky="nsew", pady=5)
    editor.columnconfigure(0, weight=1)
    editor.rowconfigure(0, weight=1)
    requirements = tk.Text(editor, height=8, width=45, wrap="word", padx=9, pady=7,
                           relief="solid", borderwidth=1, foreground="#1a2940")
    scrollbar = ttk.Scrollbar(editor, orient="vertical", command=requirements.yview)
    requirements.configure(yscrollcommand=scrollbar.set)
    requirements.insert("1.0", values["requirements"])
    requirements.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    research.rowconfigure(3, weight=1)

    model = tab("Model", "Choose a backend and the Linux account that runs literature searches.")
    field(model, 1, "reader_user")
    field(model, 2, "model_provider", ("codex", "openrouter"))
    field(model, 3, "model")
    field(model, 4, "openrouter_key")
    ttk.Button(model, text="Browse OpenRouter models",
               command=lambda: webbrowser.open("https://openrouter.ai/models")).grid(row=5, column=1, sticky="w", pady=6)

    destinations = tab("Destinations", "To keep saved publisher credentials, leave Zotero user ID, API key and webhook blank together.")
    for row, name in enumerate(("zotero_collection_name", "zotero_collection_key", "zotero_user_id",
                                "zotero_api_key", "discord_channel_id", "discord_webhook_url"), start=1):
        field(destinations, row, name)
    links = ttk.Frame(destinations)
    links.grid(row=7, column=1, sticky="w", pady=6)
    for label, url in (
        ("Zotero API keys", "https://www.zotero.org/settings/keys"),
        ("Find Discord channel ID", "https://support.discord.com/hc/en-us/articles/206346498"),
    ):
        ttk.Button(links, text=label, command=lambda target=url: webbrowser.open(target)).pack(side="left", padx=(0, 7))

    timing = tab("Schedule", "Choose when to start the daily search in local time. Delivery follows once the search and checks finish.")
    field(timing, 1, "publish_time")
    field(timing, 2, "timezone", ("Europe/Paris", "UTC", "Asia/Shanghai", "Asia/Tokyo",
                                  "America/New_York", "America/Los_Angeles", "Europe/London"), editable=True)

    footer = ttk.Frame(window, padding=(18, 10))
    footer.pack(fill="x", side="bottom")
    status = tk.StringVar(value="Save your setup, then run a read-only test.")
    ttk.Label(footer, textvariable=status, style="Hint.TLabel", wraplength=720).pack(anchor="w", pady=(0, 6))
    actions = ttk.Frame(footer)
    actions.pack(fill="x")
    completed = queue.Queue()
    busy = False
    saved = False
    tested = False

    def changed(*_):
        nonlocal saved, tested
        if busy:
            return
        saved = tested = False
        refresh()

    def refresh():
        save_button.configure(state="disabled" if busy else "normal")
        test_button.configure(state="normal" if saved and not busy else "disabled")
        enable_button.configure(state="normal" if tested and not busy else "disabled")

    def run_task(label, function, on_success):
        nonlocal busy
        busy = True
        status.set(label)
        for widget in input_widgets:
            widget.state(["disabled"])
        requirements.configure(state="disabled")
        refresh()

        def worker():
            try:
                completed.put((None, function()))
            except Exception as error:
                completed.put((error, None))

        def poll():
            nonlocal busy
            try:
                error, result = completed.get_nowait()
            except queue.Empty:
                window.after(100, poll)
                return
            if error is None:
                on_success(result)
            else:
                detail = str(error) if isinstance(error, ValueError) else (
                    "Check the terminal for details. Run sudo -v again if administrator authentication expired."
                )
                status.set("Action failed.")
                messagebox.showerror("Daily Papers", detail)
            for widget in input_widgets:
                widget.state(["!disabled"])
            requirements.configure(state="normal")
            busy = False
            refresh()

        threading.Thread(target=worker, daemon=True).start()
        window.after(100, poll)

    def submit():
        data = {name: variable.get() for name, variable in entries.items()}
        data["requirements"] = requirements.get("1.0", "end-1c")
        try:
            validated(data)
        except ValueError as error:
            messagebox.showerror("Daily Papers", str(error))
            return
        if not messagebox.askyesno("Save setup", "Install or update Daily Papers on this host?"):
            return

        def done(_):
            nonlocal saved, tested
            for name in SECRET_FIELDS | {"zotero_user_id"}:
                entries[name].set("")
            saved, tested = True, False
            status.set("Setup saved. Run a read-only test.")

        run_task("Saving configuration…", lambda: save(data), done)

    def test():
        def done(result):
            nonlocal tested
            tested = True
            status.set("Read-only test finished. Review the preview before enabling the timer.")
            preview = tk.Toplevel(window)
            preview.title("Daily Papers · Read-only preview")
            output = tk.Text(preview, width=78, height=22, wrap="word", padx=12, pady=12)
            output.pack(fill="both", expand=True)
            output.insert("1.0", result.stdout or "No output.")
            output.configure(state="disabled")

        run_task("Searching and checking the batch. This can take several minutes…",
                 lambda: privileged(DRY_RUN, stdout=subprocess.PIPE, text=True), done)

    def enable():
        if not messagebox.askyesno("Enable schedule", "Enable recurring Zotero archiving and Discord posting?"):
            return

        def done(_):
            status.set("Schedule enabled. The next run will follow your saved local time.")

        run_task("Enabling the schedule…",
                 lambda: privileged(("/usr/bin/systemctl", "enable", "--now", "daily-papers.timer")), done)

    save_button = ttk.Button(actions, text="Save setup", style="Primary.TButton", command=submit)
    test_button = ttk.Button(actions, text="Run read-only test", command=test)
    enable_button = ttk.Button(actions, text="Enable schedule", command=enable)
    for button in (save_button, test_button, enable_button):
        button.pack(side="left", padx=(0, 8))
    for variable in entries.values():
        variable.trace_add("write", changed)
    requirements.bind("<<Modified>>", lambda event: (
        requirements.edit_modified(False), changed()
    ) if requirements.edit_modified() else None)
    requirements.edit_modified(False)
    refresh()
    window.mainloop()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--terminal", action="store_true")
    mode.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.apply:
            if os.geteuid() != 0:
                raise ValueError("The setup helper requires administrator privileges.")
            apply_settings(validated(json.load(sys.stdin)))
        elif args.terminal:
            terminal(defaults())
        else:
            gui(defaults())
    except (ImportError, OSError, subprocess.SubprocessError) as error:
        print(f"Setup failed ({type(error).__name__}). Use --terminal on a headless host. Run sudo -v before setup.", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Setup cancelled.", file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
