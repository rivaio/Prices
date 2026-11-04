"""Runs on GitHub Actions (no PC needed). Each run stays alive ~4.5 minutes and checks Telegram every 15 seconds,
so a price list is picked up about 30 seconds after you send it. Rebuilds both pages and tells you in private."""
import subprocess
import time
import requests
import bot  # reuses parse() and build()

API = bot.API
MIN_AGE = 25      # wait until the newest message is 25 seconds old, so a list sent in parts is complete
RUN_SECONDS = 270  # how long one run keeps checking
POLL = 15          # seconds between checks


def publish():
    run = lambda *a: subprocess.run(a, check=True)
    run("git", "config", "user.name", "price-bot")
    run("git", "config", "user.email", "price-bot@users.noreply.github.com")
    run("git", "add", "index.html", "espace.html")
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
        run("git", "commit", "-m", "update prices")
        run("git", "push")
        print("pushed to GitHub")


def check():
    r = requests.get(f"{API}/getUpdates", params={"timeout": 0, "limit": 100}, timeout=30).json()
    if not r.get("ok"):
        raise SystemExit(f"TELEGRAM ERROR: {r.get('error_code')} {r.get('description')} (check the BOT_TOKEN secret, or stop other bots using this token)")
    ups = r.get("result", [])
    if not ups:
        return False
    msgs = []
    for u in ups:
        m = u.get("message") or u.get("channel_post") or u.get("edited_channel_post")
        if m and "text" in m:
            msgs.append(m)
    last_id = ups[-1]["update_id"]
    if msgs and time.time() - max(m["date"] for m in msgs) < MIN_AGE:
        print("newest message is too fresh, waiting"); return False

    lines, state = [], {}
    for m in msgs:
        chat = m["chat"]["id"]
        sender = (m.get("from") or {}).get("id")
        if m["text"].strip() == "/id":  # works for anyone: tells you the id to put in ALLOWED_IDS
            bot.say(bot.NOTIFY_ID or chat, f"chat id: {chat}\nyour id: {sender}"); continue
        if not ({chat, sender} & bot.ALLOWED_IDS):
            continue
        lines += bot.parse(m["text"], state)

    requests.get(f"{API}/getUpdates", params={"offset": last_id + 1, "timeout": 0}, timeout=30)  # mark as read
    if lines:
        bot.build(lines)
        bot.say(bot.NOTIFY_ID or chat, f"\u2705 {len(lines)} prices updated. The site refreshes in about 1 minute.")
        print(f"built pages with {len(lines)} prices")
        return True
    print("no price list found in the new messages")
    return False


def main():
    end = time.time() + RUN_SECONDS
    while True:
        if check():
            publish()
        if time.time() > end:
            break
        time.sleep(POLL)


if __name__ == "__main__":
    main()
