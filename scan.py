‎# סורק דירות להשכרה בחדרה - שולח התראה לטלגרם על כל מודעה חדשה שמתאימה
import json
import os
import re
import time

import requests

‎# ===== התנאים שלך (אפשר לשנות כאן) =====
SEARCH_URL = "https://www.yad2.co.il/realestate/rent?city=6500&minPrice=5000&maxPrice=7000&minRooms=4"
CITY = "חדרה"
MIN_PRICE = 5000
MAX_PRICE = 7000
MIN_ROOMS = 4
STREET_KEYWORDS = ["בגין", "זהבי"]          # גבעת אולגה: מנחם בגין, רחבעם זהבי
NEIGHBORHOOD_KEYWORDS = ["עין הים"]         # כל השכונה
# ========================================

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = "seen.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
}


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


def fetch_listings():
    r = requests.get(SEARCH_URL, headers=HEADERS, timeout=30)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        return None, r.status_code
    data = json.loads(m.group(1))
    items = {}

    def walk(o):
        if isinstance(o, dict):
            tok = o.get("token")
            if isinstance(tok, str) and ("address" in o or "price" in o):
                items[tok] = o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return list(items.values()), r.status_code


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
        "city": text_of(dig(item, "address", "city")),
        "neighborhood": text_of(dig(item, "address", "neighborhood")),
        "street": text_of(dig(item, "address", "street")),
        "house": dig(item, "address", "house", "number"),
        "floor": dig(item, "address", "house", "floor"),
        "price": to_number(item.get("price")),
        "rooms": to_number(dig(item, "additionalDetails", "roomsCount")) or to_number(item.get("rooms")),
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
    lines = ["🏠 דירה חדשה שמתאימה לך!"]
    if address:
        lines.append(f"📍 {address}" + (f", {d['neighborhood']}" if d["neighborhood"] else ""))
    if d["price"]:
        lines.append(f"💰 {int(d['price']):,} ₪")
    if d["rooms"]:
        lines.append(f"🛏 {d['rooms']:g} חדרים" + (f" | {d['sqm']} מ\"ר" if d["sqm"] else ""))
    if d["floor"] is not None:
        lines.append(f"🏢 קומה {d['floor']}")
    lines.append(f"🔗 https://www.yad2.co.il/realestate/item/{d['token']}")
    return "\n".join(lines)


def main():
    state = load_state()
    first_run = state is None
    if first_run:
        state = {"seen": [], "blocked_notified": False}

    listings, status = fetch_listings()
    if listings is None:
        print("could not read Yad2, status:", status)
        if not state.get("blocked_notified"):
            send("⚠️ הסורק לא הצליח לקרוא את יד2 כרגע (ייתכן חסימה). ממשיך לנסות.")
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
        send(f"✅ הסורק פעיל! נסרקו {len(listings)} מודעות בחדרה, "
             f"מתוכן {len(new_matches)} מתאימות לתנאים שלך.")

    for d in new_matches[:15]:
        send(format_message(d))
        time.sleep(1)

    state["seen"] = state["seen"][-5000:]
    save_state(state)


if __name__ == "__main__":
    main()
