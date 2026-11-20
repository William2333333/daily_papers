# Daily Papers v2.3

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
2. Select **Save setup**, then **Test connections**. The app independently checks the model, Zotero collection and key permissions, Discord channel, and whether the reader can open the publisher credential file. This works without any candidates. A model check can incur provider usage.
3. Select **Run read-only test**, then review the paper cards. The test performs a fresh search and deduplicates its results without archiving or posting.
4. Under **Schedule**, choose `auto` for recurring archiving and sending, or `review` to generate a preview on each scheduled run. Then select **Enable schedule**. The configured time starts the search. In review mode, select eligible papers and click **Publish selected** in Operations.

Review language options are English, 简体中文, Français, Español, and 日本語. Paper titles retain their original spelling. The model is asked to review at most three full texts and label abstract-only reviews.

## Upgrade or edit settings

Pause the timer before changing an existing setup, and let an active publisher finish:

```bash
sudo systemctl stop daily-papers.timer
systemctl is-active daily-papers.service
```

Wait for `inactive`, then reopen the setup app. Existing public settings are loaded automatically. Leave the Zotero user ID, API key, and webhook all blank to keep existing publisher credentials. Leave the OpenRouter key blank to reuse a saved reader key. Save, test, and enable the schedule again. The app writes a systemd timer drop-in when the schedule changes.

## Operations

Open **Operations** to see the next and last run, the last batch's counts, and pending or confirmed Discord deliveries. Use **Pause timer** or **Resume timer** to change scheduling. A newer search replaces the previous review preview. Approving papers rechecks Zotero and validates the destination before publication.

If Discord fails after Zotero archives a paper, v2.3 retains the candidate text and Zotero item ID in the publisher's private state. In **Discord deliveries**, verify the message is absent in the channel, then click **Retry Discord only**. A confirmed send stores its Discord message ID. v2.3 cannot reconstruct delivery records that were lost before the upgrade.


```bash
systemctl list-timers --all daily-papers.timer
sudo journalctl -u daily-papers.service -n 80 --no-pager
sudo /bin/bash /opt/daily-papers/run_daily_papers.sh --dry-run
sudo systemctl disable --now daily-papers.timer
```

Use `sudo systemctl start daily-papers.service` to start a fresh run in the saved mode. Only `auto` posts directly.

The reader and publisher use distinct Linux accounts. The reader must lack administrative access and access to the publisher’s private credentials. Verify that isolation from the reader account before enabling scheduled publication. The setup app stores OpenRouter credentials under the reader account and Zotero/Discord credentials under `daily-publisher`, with file mode `0600`.

An empty batch means no suitable papers were found. Nonempty batches check Zotero by DOI, arXiv ID, and normalized title, and verify the webhook channel. If a result is `discord_unconfirmed`, Zotero already saved the item. Check Discord before retrying. With the original candidate JSON, run `publisher.py --resume-discord ITEM_KEY --discord-channel-id CHANNEL_ID --credentials-file PATH CANDIDATE.json` under the publisher account to retry Discord only.

## Tests

Run `python3 -B -m unittest discover -s tests -v`. The GitHub Actions workflow runs the offline suite and opens both GUI windows under Xvfb. The tests require no service credentials.
