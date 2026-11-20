# Daily Papers

[English](README.md)

在**你自己的研究方向**中发现新论文。填写感兴趣的主题，选择模型和简读语言，再设置每天的运行时间。Daily Papers 会在 Zotero 中查重、归档新论文，并把简读发送到你的 Discord 频道。

## 快速设置

在支持 systemd 的 Linux 主机上准备 Bash、Python 3.9 或更新版本，以及 HTTPS 出站访问。还需要：

- 一个已有的非管理员 Linux 阅读账号。选择 Codex 时，在该账号登录 Codex CLI。选择 OpenRouter 时，准备 API 密钥，并从[模型目录](https://openrouter.ai/models)复制支持网页工具的模型 ID。
- 已同步的 Zotero 个人库。从分类网页链接复制分类 ID，并准备数字用户 ID 和具有个人库读写权限的[专用密钥](https://www.zotero.org/settings/keys)。
- 目标 [Discord 频道 ID](https://support.discord.com/hc/en-us/articles/206346498)，以及为该频道创建的 webhook。

在部署主机上，从可信的项目目录运行：

```bash
sudo -v
/usr/bin/python3 -B setup_wizard.py
```

图形窗口需要 Linux 桌面和 Tkinter。通过 SSH 操作时，在最后一行加上 `--terminal`，即可使用同一套设置。Debian/Ubuntu 的 Tkinter 软件包通常叫 `python3-tk`。

1. **填写设置**。在「Research」填写研究主题、纳入和排除条件、简读语言及最多篇数。篇数可选 **1–10**，默认 **2**。「Schedule」可以设置每天的启动时间和 IANA 时区，默认 **09:00 / Europe/Paris**。
2. **保存并试跑**。点击 **Save setup → Run read-only test**。系统会检索、查重并给出预览，试跑不会归档或发送。
3. **启用调度**。准备好启用真实归档和推送后，点击 **Enable schedule**。设定时间指检索的启动时间，论文会在检索和查重完成后发送。

简读可选英语、简体中文、法语、西班牙语、日语。论文标题保留原文。模型会被要求每次最多审阅三篇全文，并标明仅阅读摘要的条目。

## 升级或修改设置

如已启用定时器，先暂停并等待正在执行的发布结束：

```bash
sudo systemctl stop daily-papers.timer
systemctl is-active daily-papers.service
```

看到 `inactive` 后重新打开设置向导。界面会预填现有公开配置。如需保留凭据，将 Zotero 用户 ID、密钥和 webhook 一起留空。已有 OpenRouter 密钥也可以留空保留。保存后重新试跑并启用调度，时间和时区会写入 systemd 的定时配置。

## 运维命令

```bash
systemctl list-timers --all daily-papers.timer
sudo journalctl -u daily-papers.service -n 80 --no-pager
sudo /bin/bash /opt/daily-papers/run_daily_papers.sh --dry-run
sudo systemctl disable --now daily-papers.timer
```

如需马上执行一次真实发布，运行 `sudo systemctl start daily-papers.service`。它会重新检索，符合条件的新论文可能被归档并发送。

模型密钥存放在阅读账号的私有文件中，Zotero 和 Discord 凭据存放在 `daily-publisher` 的私有文件中，权限均为 `0600`。启用调度前，从阅读身份验证它没有管理员权限且无法读取发布凭据。更多补发说明见 [英文 README](README.md#operations)。
