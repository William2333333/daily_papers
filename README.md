# Daily Papers

[简体中文](README.zh-CN.md)

Find new papers in **your own research area**. Describe the topics you want to follow, choose a model and review language, and set when the search should run. Daily Papers checks Zotero for duplicates, archives new candidates, and sends short reviews to your Discord channel.

## Quick setup

Use a Linux host with systemd, Bash, Python 3.9 or newer, and outbound HTTPS. Prepare:

- An existing non-admin Linux account for literature searches. For Codex, sign in to the Codex CLI under that account. For OpenRouter, prepare an API key and an exact model ID from the [model catalog](https://openrouter.ai/models). Choose a model that supports OpenRouter web tools.
- A synced Zotero personal library. Copy the collection key from the collection URL. Obtain your numeric user ID and a [key with personal-library read and write access](https://www.zotero.org/settings/keys).
- The [Discord channel ID](https://support.discord.com/hc/en-us/articles/206346498) and a webhook created for that channel.

On the deployment host, run the setup app from a trusted checkout:

```bash
sudo -v
/usr/bin/python3 -B setup_wizard.py
```

The app requires a graphical session and Python Tkinter (usually `python3-tk` on Debian/Ubuntu). On SSH or a headless host, add `--terminal` to the same command.

1. Enter your research requirements, model, and destinations in the form. Choose a review language and a maximum of **1–10 papers per run**, and enter the daily start time and IANA time zone. Defaults are two papers, 09:00, and `Europe/Paris`.
2. Select **Save setup**, then **Run read-only test**. Saving installs the service and writes the settings. The test searches and previews the results without archiving or posting.
3. Select **Enable schedule** when ready for recurring archiving and posting. The configured time is the start of the search. Delivery follows when the run completes.

Review language options are English, 简体中文, Français, Español, and 日本語. Paper titles retain their original spelling. The model is asked to review at most three full texts and label abstract-only reviews.

## Upgrade or edit settings

Pause the timer before changing an existing setup, and let an active publisher finish:

```bash
sudo systemctl stop daily-papers.timer
systemctl is-active daily-papers.service
```

Wait for `inactive`, then reopen the setup app. Existing public settings are loaded automatically. Leave the Zotero user ID, API key, and webhook all blank to keep existing publisher credentials. Leave the OpenRouter key blank to reuse a saved reader key. Save, test, and enable the schedule again. The app writes a systemd timer drop-in when the schedule changes.

## Operations

```bash
systemctl list-timers --all daily-papers.timer
sudo journalctl -u daily-papers.service -n 80 --no-pager
sudo /bin/bash /opt/daily-papers/run_daily_papers.sh --dry-run
sudo systemctl disable --now daily-papers.timer
```

Use `sudo systemctl start daily-papers.service` to explicitly run a live publication at any time. This performs a fresh search and may archive and send new candidates.

The reader and publisher use distinct Linux accounts. The reader must lack administrative access and access to the publisher’s private credentials. Verify that isolation from the reader account before enabling scheduled publication. The setup app stores OpenRouter credentials under the reader account and Zotero/Discord credentials under `daily-publisher`, with file mode `0600`.

An empty batch means no suitable papers were found. Nonempty batches check Zotero by DOI, arXiv ID, and normalized title, and verify the webhook channel. If a result is `discord_unconfirmed`, Zotero already saved the item. Check Discord before retrying. With the original candidate JSON, run `publisher.py --resume-discord ITEM_KEY --discord-channel-id CHANNEL_ID --credentials-file PATH CANDIDATE.json` under the publisher account to retry Discord only.
