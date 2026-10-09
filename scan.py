# Hadera rental apartment scanner - sends a Telegram alert for every new matching listing
import json
import os
import re
import time

import requests

# ===== Your search conditions (edit here) =====
CITY_CODE = 6500  # Hadera
CITY = "\u05d7\u05d3\u05e8\u05d4"
MIN_PRICE = 5000
MAX_PRICE = 7000
MIN_ROOMS = 4
STREET_KEYWORDS = ["\u05d1\u05d2\u05d9\u05df", "\u05d6\u05d4\u05d1\u05d9"]          # Givat Olga: Menachem Begin, Rehavam Zeevi
NEIGHBORHOOD_KEYWORDS = ["\u05e2\u05d9\u05df \u05d4\u05d9\u05dd"]         # whole neighborhood
# ==============================================

SOURCES = [
    ("page", f"https://www.yad2.co.il/realestate/rent?city={CITY_CODE}&minPrice={MIN_PRICE}&maxPrice={MAX_PRICE}&minRooms={MIN_ROOMS}"),
    ("api", f"https://gw.yad2.co.il/realestate-feed/rent/map?city={CITY_CODE}&minPrice={MIN_PRICE}&maxPrice={MAX_PRICE}&minRooms={MIN_ROOMS}"),
    ("api", f"https://gw.yad2.co.il/feed-search-legacy/realestate/rent?city={CITY_CODE}&price={MIN_PRICE}-{MAX_PRICE}&rooms={MIN_ROOMS}--1"),
]

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = "seen.json"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
PAGE_HEADERS = {**BROWSER_HEADERS,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1"}
API_HEADERS = {**BROWSER_HEADERS,
               "Accept": "application/json, text/plain, */*",
               "Origin": "https://www.yad2.co.il",
               "Referer": "https://www.yad2.co.il/realestate/rent"}


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20,
    )
    print("telegram:", r.status_code)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def extract_items(data):
    items = {}

    def walk(o):
        if isinstance(o, dict):
            tok = o.get("token") or o.get("link_token")
            if isinstance(tok, str) and ("address" in o or "price" in o):
                o["token"] = tok
                items[tok] = o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return list(items.values())


def diagnose(text):
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    title = title.group(1).strip()[:80] if title else "-"
    low = text.lower()
    blocked = any(w in low for w in ["captcha", "shieldsquare", "radware", "perfdrive", "are you for real"])
    return f"title={title!r} blocked_words={blocked} length={len(text)}"


def fetch_listings():
    session = requests.Session()
    try:
        session.get("https://www.yad2.co.il/", headers=PAGE_HEADERS, timeout=30)
    except Exception as e:
        print("homepage error:", e)

    for kind, url in SOURCES:
        try:
            r = session.get(url, headers=PAGE_HEADERS if kind == "page" else API_HEADERS, timeout=30)
        except Exception as e:
            print(f"[{kind}] error: {e}")
            continue
        print(f"[{kind}] status {r.status_code} | {url}")
        data = None
        if kind == "page":
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
            if m:
                data = json.loads(m.group(1))
        else:
            try:
                data = r.json()
            except ValueError:
                pass
        if data is None:
            print("   not readable:", diagnose(r.text))
            continue
        items = extract_items(data)
        print(f"   found {len(items)} listings")
        if items:
            return items
    return None


def dig(o, *path):
    for p in path:
        if not isinstance(o, dict):
            return None
        o = o.get(p)
    return o


def text_of(v):
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or "")
    return str(v or "")


def to_number(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def describe(item):
    return {
        "token": item["token"],
        "city": text_of(dig(item, "address", "city")) or text_of(item.get("city")),
        "neighborhood": text_of(dig(item, "address", "neighborhood")) or text_of(item.get("neighborhood")),
        "street": text_of(dig(item, "address", "street")) or text_of(item.get("street")),
        "house": dig(item, "address", "house", "number"),
        "floor": dig(item, "address", "house", "floor"),
        "price": to_number(item.get("price")),
        "rooms": to_number(dig(item, "additionalDetails", "roomsCount")) or to_number(item.get("rooms") or item.get("Rooms")),
        "sqm": dig(item, "additionalDetails", "squareMeter"),
        "flat": json.dumps(item, ensure_ascii=False),
    }


def matches(d):
    if CITY not in (d["city"] or d["flat"]):
        return False
    if d["price"] is not None and not (MIN_PRICE <= d["price"] <= MAX_PRICE):
        return False
    if d["rooms"] is not None and d["rooms"] < MIN_ROOMS:
        return False
    location = f'{d["street"]} {d["neighborhood"]}'.strip() or d["flat"]
    return any(k in location for k in STREET_KEYWORDS + NEIGHBORHOOD_KEYWORDS)


def format_message(d):
    address = " ".join(str(x) for x in [d["street"], d["house"] or ""] if x).strip()
    lines = ["\U0001f3e0 \u05d3\u05d9\u05e8\u05d4 \u05d7\u05d3\u05e9\u05d4 \u05e9\u05de\u05ea\u05d0\u05d9\u05de\u05d4 \u05dc\u05da!"]
    if address:
        lines.append(f"\U0001f4cd {address}" + (f", {d['neighborhood']}" if d["neighborhood"] else ""))
    if d["price"]:
        lines.append(f"\U0001f4b0 {int(d['price']):,} \u20aa")
    if d["rooms"]:
        lines.append(f"\U0001f6cf {d['rooms']:g} \u05d7\u05d3\u05e8\u05d9\u05dd" + (f" | {d['sqm']} \u05de\"\u05e8" if d["sqm"] else ""))
    if d["floor"] is not None:
        lines.append(f"\U0001f3e2 \u05e7\u05d5\u05de\u05d4 {d['floor']}")
    lines.append(f"\U0001f517 https://www.yad2.co.il/realestate/item/{d['token']}")
    return "\n".join(lines)


def main():
    state = load_state()
    first_run = state is None or not state.get("seen")
    if state is None:
        state = {"seen": [], "blocked_notified": False}

    listings = fetch_listings()
    if listings is None:
        print("could not read Yad2 from any source")
        if not state.get("blocked_notified"):
            send("\u26a0\ufe0f \u05d4\u05e1\u05d5\u05e8\u05e7 \u05dc\u05d0 \u05d4\u05e6\u05dc\u05d9\u05d7 \u05dc\u05e7\u05e8\u05d5\u05d0 \u05d0\u05ea \u05d9\u05d32 \u05db\u05e8\u05d2\u05e2 (\u05d9\u05d9\u05ea\u05db\u05df \u05d7\u05e1\u05d9\u05de\u05d4). \u05de\u05de\u05e9\u05d9\u05da \u05dc\u05e0\u05e1\u05d5\u05ea.")
            state["blocked_notified"] = True
        save_state(state)
        return
    state["blocked_notified"] = False

    seen = set(state["seen"])
    new_matches = []
    for item in listings:
        tok = item["token"]
        if tok in seen:
            continue
        seen.add(tok)
        state["seen"].append(tok)
        d = describe(item)
        if matches(d):
            new_matches.append(d)

    print(f"found {len(listings)} listings, {len(new_matches)} new matches")
    if first_run:
        send(f"\u2705 \u05d4\u05e1\u05d5\u05e8\u05e7 \u05e4\u05e2\u05d9\u05dc! \u05e0\u05e1\u05e8\u05e7\u05d5 {len(listings)} \u05de\u05d5\u05d3\u05e2\u05d5\u05ea \u05d1\u05d7\u05d3\u05e8\u05d4, "
             f"\u05de\u05ea\u05d5\u05db\u05df {len(new_matches)} \u05de\u05ea\u05d0\u05d9\u05de\u05d5\u05ea \u05dc\u05ea\u05e0\u05d0\u05d9\u05dd \u05e9\u05dc\u05da.")

    for d in new_matches[:15]:
        send(format_message(d))
        time.sleep(1)

    state["seen"] = state["seen"][-5000:]
    save_state(state)


if __name__ == "__main__":
    main()
