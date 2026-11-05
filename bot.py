"""Telegram bot: reads the price list you send, rebuilds index.html (iZone) and espace.html (Espace +5$), uploads it to GitHub Pages."""
import base64, os, re, sys, time
import requests

BOT_TOKEN    = os.environ["BOT_TOKEN"]                         # from @BotFather
ALLOWED_IDS  = {int(x) for x in os.environ["ALLOWED_IDS"].split(",")}  # your user id / channel id
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")              # optional: auto-upload
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")               # e.g. "hama/prices"
PAGE_URL     = os.environ.get("PAGE_URL", "")                  # e.g. "https://hama.github.io/prices/"
NOTIFY_ID    = next((i for i in sorted(ALLOWED_IDS) if i > 0), None)  # replies go to you, never to the channel
# one page per store: file name -> dollars added to every price
STORES = {
    "index.html":  int(os.environ.get("MARKUP_IZONE", "0")),   # iZone  (no markup)
    "espace.html": int(os.environ.get("MARKUP_ESPACE", "5")),  # Espace (+5$)
}
WAIT_SECONDS = 15   # after the last message, wait this long, then publish

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
HEADER = re.compile(r"^\W*(iPhone[\w ]*?)\s+(\d+\s?(?:GB|TB))\b.*$", re.I)
PREFIX = re.compile(r"^\[[^\]]*\][^:]*:\s*")  # "[11/3/2026 7:48 PM] Name: " from Telegram exports
ITEM = re.compile(r"^\W*([A-Za-z][A-Za-z ]*?)\s+\$?\s*(\d+)\s*\$?\s+([A-Za-z]+(?:-[A-Za-z]+)?)\s*(.*)$")
FIX = {"GLAICER": "Glacier", "GLACIER": "Glacier"}


def parse(text, state):
    """state = {'model','storage'}; returns RAW lines."""
    out = []
    for line in text.replace("\u0130", "I").splitlines():
        line = PREFIX.sub("", line.strip())
        if not line:
            continue
        h = HEADER.match(line)
        if h:
            state["model"], state["storage"] = re.sub(r"^iphone", "iPhone", h.group(1).strip(), flags=re.I), h.group(2).replace(" ", "").upper()
            continue
        m = ITEM.match(line)
        if m and state.get("model"):
            color, price, code, rest = m.groups()
            color = FIX.get(color.strip().upper(), color.strip())
            note = ""
            if "\u26a0" in rest:  # warning sign -> keep as note
                note = "\u26a0\ufe0f User note: " + rest.replace("\u26a0\ufe0f", "").replace("\u26a0", "").strip()
            out.append("|".join([state["model"], state["storage"], color, price, code.upper()] + ([note] if note else [])))
    return out


def apply_markup(lines, extra):
    out = []
    for l in lines:
        p = l.split("|")
        if int(p[3]) > 0:  # keep 0 = unavailable
            p[3] = str(int(p[3]) + extra)
        out.append("|".join(p))
    return out


PASSWORDS = {  # optional: only people who know the password can open the page (prices are encrypted)
    "index.html":  os.environ.get("PASS_IZONE", ""),
    "espace.html": os.environ.get("PASS_ESPACE", ""),
}


def encrypt(text, password):
    import json, secrets
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    iters, salt, iv = 200000, secrets.token_bytes(16), secrets.token_bytes(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iters).derive(password.encode())
    ct = AESGCM(key).encrypt(iv, text.encode(), None)
    e = lambda x: base64.b64encode(x).decode()
    return json.dumps({"s": e(salt), "v": e(iv), "c": e(ct), "i": iters})


def decrypt(enc_json, password):
    import json
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    d = json.loads(enc_json)
    d64 = lambda x: base64.b64decode(x)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=d64(d["s"]), iterations=d["i"]).derive(password.encode())
    return AESGCM(key).decrypt(d64(d["v"]), d64(d["c"]), None).decode()


def load_existing():
    """Reads the prices already published in index.html (iZone) and removes its markup.
    Returns a list of RAW lines, or None if they can not be read."""
    name = "index.html"
    try:
        html = open(name, encoding="utf-8").read()
        e = re.search(r"const ENC=(null|\{[^}]*\});", html)
        if not e:
            return None
        if e.group(1) == "null":
            p = re.search(r"const PLAIN=`(.*?)`;", html, re.S)
            raw = p.group(1) if p else ""
        else:
            raw = decrypt(e.group(1), PASSWORDS.get(name, ""))
        lines = [l for l in raw.split("\n") if l.strip()]
        extra = STORES[name]
        if extra:
            for i, l in enumerate(lines):
                p = l.split("|")
                if int(p[3]) > 0:
                    p[3] = str(int(p[3]) - extra)
                lines[i] = "|".join(p)
        return lines
    except Exception as e:
        print("could not read existing prices:", e)
        return None


def merge(old, new):
    """Keeps every old price; new lines replace the same item (model+storage+color+code) or are added."""
    key = lambda l: tuple(l.split("|")[:3] + [l.split("|")[4]])
    d = {key(l): l for l in old}
    updated = added = 0
    for l in new:
        k = key(l)
        if k in d:
            if d[k] != l:
                updated += 1
        else:
            added += 1
        d[k] = l
    return list(d.values()), updated, added


def build(lines):
    """Writes one html file per store. Returns {filename: html}."""
    tpl = open(os.path.join(os.path.dirname(__file__), "template.html"), encoding="utf-8").read()
    pages = {}
    for name, extra in STORES.items():
        raw = "\n".join(apply_markup(lines, extra))
        pw = PASSWORDS.get(name, "")
        if pw:
            html = tpl.replace("__ENC__", encrypt(raw, pw)).replace("__RAW__", "")
        else:
            html = tpl.replace("__ENC__", "null").replace("__RAW__", raw)
        with open(name, "w", encoding="utf-8") as f:
            f.write(html)
        pages[name] = html
    return pages


def upload(pages):
    if not (GITHUB_TOKEN and GITHUB_REPO):
        return False
    h = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    for name, html in pages.items():
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{name}"
        old = requests.get(url, headers=h, timeout=30)
        body = {"message": "update prices", "content": base64.b64encode(html.encode()).decode()}
        if old.status_code == 200:
            body["sha"] = old.json()["sha"]
        requests.put(url, headers=h, json=body, timeout=60).raise_for_status()
    return True


def say(chat_id, text):
    requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=30)


def main():
    offset, lines, state, last, chat = 0, [], {}, 0, None
    print("Bot is running...")
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 10}, timeout=30).json()
        except requests.RequestException:
            time.sleep(5); continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("channel_post") or u.get("edited_channel_post")
            if not msg or "text" not in msg:
                continue
            if msg["text"].strip() == "/id":  # works for anyone, so you can find your ID
                say(msg["chat"]["id"], f"your id: {(msg.get('from') or {}).get('id', msg['chat']['id'])}"); continue
            ids = {msg["chat"]["id"], (msg.get("from") or {}).get("id")}
            if not ids & ALLOWED_IDS:
                continue
            got = parse(msg["text"], state)
            if got:
                lines += got; last = time.time(); chat = NOTIFY_ID or msg["chat"]["id"]
        if lines and time.time() - last > WAIT_SECONDS:
            try:
                html = build(lines)
                pushed = upload(html)
                say(chat, f"\u2705 {len(lines)} \u0646\u0631\u062e \u0646\u0648\u06ce \u06a9\u0631\u0627\u06cc\u06d5\u0648\u06d5" + (f"\n{PAGE_URL}\n{PAGE_URL}espace.html" if pushed else "\n(index.html + espace.html \u0644\u06d5 \u0641\u06c6\u06b5\u062f\u06d5\u0631\u06cc \u0628\u06c6\u062a\u062f\u0627 \u062f\u0631\u0648\u0633\u062a \u06a9\u0631\u0627)"))
            except Exception as e:
                say(chat, f"\u274c Error: {e}")
            lines, state = [], {}


if __name__ == "__main__":
    main()
