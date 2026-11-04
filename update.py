"""Runs on GitHub Actions every ~5 minutes (no PC needed).
Reads new Telegram messages, rebuilds index.html from the price list, tells you in private."""
import time
import requests
import bot  # reuses parse() and build()

API = bot.API
MIN_AGE = 90  # wait until the newest message is 90 seconds old, so a long list sent in parts is complete


def main():
    r = requests.get(f"{API}/getUpdates", params={"timeout": 0, "limit": 100}, timeout=30).json()
    if not r.get("ok"):
        raise SystemExit(f"TELEGRAM ERROR: {r.get('error_code')} {r.get('description')} (check the BOT_TOKEN secret, or stop other bots using this token)")
    ups = r.get("result", [])
    if not ups:
        print("no new messages"); return
    msgs = []
    for u in ups:
        m = u.get("message") or u.get("channel_post") or u.get("edited_channel_post")
        if m and "text" in m:
            msgs.append(m)
    last_id = ups[-1]["update_id"]
    if msgs and time.time() - max(m["date"] for m in msgs) < MIN_AGE:
        print("newest message is too fresh, waiting for the next run"); return

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
        bot.say(bot.NOTIFY_ID or chat, f"\u2705 {len(lines)} prices updated. The site refreshes in 1-2 minutes.")
        print(f"built index.html with {len(lines)} prices")
    else:
        print("no price list found in the new messages")


if __name__ == "__main__":
    main()
