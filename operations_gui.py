"""Desktop review, scheduling status and Discord delivery recovery."""
import os
from urllib.parse import urlsplit


def show_operations(window, snapshot, action, reload):
    import tkinter as tk
    import webbrowser
    from tkinter import messagebox, ttk

    panel = tk.Toplevel(window)
    panel.title("Daily Papers · Operations")
    panel.geometry("820x620")
    notebook = ttk.Notebook(panel)
    notebook.pack(fill="both", expand=True, padx=12, pady=12)

    def tab(title):
        outer = ttk.Frame(notebook)
        notebook.add(outer, text=title)
        canvas = tk.Canvas(outer, background="white", highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas, padding=14)
        embedded = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(embedded, width=event.width))
        return inner

    def label(parent, text, **kwargs):
        ttk.Label(parent, text=text, wraplength=735, justify="left", **kwargs).pack(anchor="w", pady=3)

    def invoke(title, command, confirmation=None):
        if confirmation and not messagebox.askyesno(title, confirmation, parent=panel):
            return
        action(title, command, panel)

    def copy_original(url):
        panel.clipboard_clear()
        panel.clipboard_append(url)
        feedback.set("Original paper link copied.")

    def open_original(url):
        if os.geteuid() == 0:
            copy_original(url)
            feedback.set("Link copied. Open it in your regular browser, or restart setup without sudo.")
            return
        if not webbrowser.open(url):
            copy_original(url)
            feedback.set("No browser could be opened. The link was copied.")

    overview = tab("Run status")
    timer = snapshot["timer"]
    service = snapshot["service"]
    label(overview, f"Mode: {snapshot['mode']}    Schedule: {snapshot['time']} {snapshot['timezone']}")
    label(overview, f"Timer: {timer.get('ActiveState', 'unknown')}    Next: {timer.get('NextElapseUSecRealtime') or '—'}")
    label(overview, f"Last trigger: {timer.get('LastTriggerUSec') or '—'}    Service result: {service.get('Result') or '—'}")
    if snapshot["runs"]:
        run = snapshot["runs"][-1]
        label(overview, f"Last recorded run: {run['at']} · {run['mode']} · {'failed' if run['failed'] else 'finished'}")
        counts = run["counts"]
        label(overview, " · ".join(f"{name}: {count}" for name, count in counts.items()) or "0 candidates")
    else:
        label(overview, "No run has been recorded yet.")
    if timer.get("ActiveState") == "active":
        ttk.Button(overview, text="Pause timer",
                   command=lambda: invoke("Pausing timer", ("pause", None))).pack(anchor="w", pady=8)
    else:
        ttk.Button(overview, text="Resume timer", command=lambda: invoke(
            "Resuming timer", ("resume", None), "Resume the timer in the saved publication mode?",
        )).pack(anchor="w", pady=8)

    review_page = tab("Review papers")
    feedback = tk.StringVar()
    ttk.Label(review_page, textvariable=feedback, wraplength=735).pack(anchor="w")
    review = snapshot["review"]
    selected = {}
    if not review or not review["entries"]:
        label(review_page, "No candidates to review. Run a read-only test to generate a new preview.")
    else:
        label(review_page, f"Latest preview · {review['at']}")
        for entry in review["entries"]:
            candidate = entry.get("candidate", {})
            card = ttk.LabelFrame(review_page, text=f"{entry['index']}. {candidate.get('title', 'Candidate')}", padding=10)
            card.pack(fill="x", pady=6)
            status = entry["status"]
            if status == "ready":
                selected[entry["index"]] = tk.BooleanVar(value=False)
                ttk.Checkbutton(card, text="Select for publication",
                                variable=selected[entry["index"]]).pack(anchor="w")
            else:
                detail = entry.get("reason") or ", ".join(
                    match["item_key"] for match in entry.get("duplicates", [])
                )
                label(card, f"{status}: {detail}" if detail else status)
            url = candidate.get("url", "")
            try:
                link = urlsplit(url)
            except ValueError:
                link = None
            if link and link.scheme in ("http", "https") and link.hostname and not link.username:
                links = ttk.Frame(card)
                links.pack(anchor="w", pady=3)
                ttk.Button(links, text="Open original paper",
                           command=lambda source=url: open_original(source)).pack(side="left")
                ttk.Button(links, text="Copy link",
                           command=lambda source=url: copy_original(source)).pack(side="left", padx=(6, 0))
            if candidate.get("discord_message"):
                label(card, candidate["discord_message"])

        def approve():
            indices = [index for index, checked in selected.items() if checked.get()]
            if not indices:
                messagebox.showinfo("Review papers", "Select at least one eligible paper.", parent=panel)
                return
            invoke("Publishing selected papers", ("approve", {"review_id": review["id"], "indices": indices}),
                   f"Archive and send the {len(indices)} selected paper(s)?")

        if selected:
            ttk.Button(review_page, text="Publish selected", command=approve).pack(anchor="w", pady=8)

    deliveries = tab("Discord deliveries")
    recent = sorted(snapshot["deliveries"].values(), key=lambda entry: entry["at"], reverse=True)
    pending = [entry for entry in recent if entry["status"] == "discord_unconfirmed"]
    sent = [entry for entry in recent if entry["status"] == "published"]
    if not recent:
        label(deliveries, "No recorded deliveries.")
    for entry in pending + sent[:20]:
        key = entry["zotero_item_key"]
        title = entry["candidate"]["title"]
        card = ttk.LabelFrame(deliveries, text=title, padding=10)
        card.pack(fill="x", pady=6)
        label(card, f"Zotero {key} · {entry['status']} · {entry['at']}")
        if entry["status"] == "published":
            label(card, f"Discord message ID: {entry['discord_message_id']}")
        else:
            label(card, entry["candidate"]["discord_message"])
            ttk.Button(card, text="Retry Discord only", command=lambda item=key: invoke(
                "Retrying Discord delivery", ("retry", item),
                "Confirm you checked the target channel and the original message is absent.",
            )).pack(anchor="w", pady=4)

    ttk.Button(panel, text="Refresh", command=lambda: (panel.destroy(), reload())).pack(anchor="e", padx=12, pady=(0, 10))
