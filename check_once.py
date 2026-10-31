"""
One-shot project checker for GitHub Actions.
Runs, looks for new matching projects, sends Telegram alerts, saves state.json, exits.
Needs two secrets: BOT_TOKEN and CHAT_ID.
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

MIN_BUDGET = 10
MAX_OFFERS = 8
MAX_PER_RUN = 10

KEYWORDS = [
    "figma", "فيجما", "فيغما",
    "front-end", "frontend", "front end", "فرونت اند", "فرونت إند",
    "landing page", "لاندينج بيج", "صفحة هبوط", "صفحة الهبوط",
    "angular", "انجولار", "أنجولار",
    "ui/ux", "ui ux", "تصميم واجهات", "تصميم واجهة", "واجهة مستخدم", "واجهات المستخدم",
    "html", "css", "javascript", "typescript",
    "تصميم موقع", "تصميم مواقع",
]
EXCLUDED = [
    "php", "android", "wordpress", "shopify", "wp",
    "ووردبريس", "وردبريس", "اندرويد", "أندرويد", "شوبيفاي",
]

SOURCES = {
    "مستقل": {"url": "https://mostaql.com/projects?sort=latest", "base": "https://mostaql.com"},
    "نفذلي": {"url": "https://nafezly.com/projects", "base": "https://nafezly.com"},
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PersonalFreelanceAlerts/1.0)",
    "Accept-Language": "ar,en;q=0.8",
}
STATE_FILE = Path("state.json")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def send(text):
    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20,
    )
    response.raise_for_status()


def has_word(text, words):
    return any(re.search(rf"(?<!\w){re.escape(w.lower())}(?!\w)", text) for w in words)


def is_match(project):
    text = (project["title"] + " " + project["description"]).lower()
    if has_word(text, EXCLUDED) or not has_word(text, KEYWORDS):
        return False
    if project["budget"] is not None and project["budget"] < MIN_BUDGET:
        return False
    if project["offers"] is not None and project["offers"] > MAX_OFFERS:
        return False
    return True


def parse_budget(text):
    pattern_a = r"\$\s*([\d,]+(?:\.\d+)?)"
    pattern_b = r"([\d,]+(?:\.\d+)?)\s*(?:دولار|\$)"
    nums = [float(n.replace(",", "")) for n in re.findall(pattern_a, text) + re.findall(pattern_b, text)]
    return max(nums) if nums else None


def parse_offers(text):
    m = re.search(r"(\d+)\s*(?:عروض|عرض)", text) or re.search(r"(?:عروض|عرض)\s*[:：]?\s*(\d+)", text)
    return int(m.group(1)) if m else None


def best_container(link):
    """Climb up from the title link until the block that holds the budget/offers text,
    but stop before it would contain more than one project."""
    node = link
    for _ in range(6):
        parent = node.parent
        if parent is None or parent.name in ("body", "html"):
            break
        hrefs = {a.get("href") for a in parent.select("a[href*='/project/']")}
        if len(hrefs) > 1:
            break
        node = parent
        if re.search(r"عروض|عرض|\$|دولار", node.get_text(" ", strip=True)):
            break
    return node


def fetch_source(name, cfg):
    response = requests.get(cfg["url"], headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    base = cfg["base"]

    pairs = []
    for row in soup.select("tr.project-row, .project-row"):
        link = row.select_one("h2 a, h3 a, a[href*='/project/']")
        if link:
            pairs.append((row, link))
    if not pairs:
        for link in soup.select("a[href*='/project/']"):
            pairs.append((best_container(link), link))

    projects, seen_urls = [], set()
    for container, link in pairs:
        title = link.get_text(" ", strip=True)
        href = link.get("href", "")
        href = base + href if href.startswith("/") else href
        if not title or not href or "/project/" not in href or href in seen_urls:
            continue
        seen_urls.add(href)
        text = container.get_text(" ", strip=True)
        projects.append({
            "title": title,
            "description": text,
            "url": href,
            "budget": parse_budget(text),
            "offers": parse_offers(text),
            "source": name,
        })
    return projects


def main():
    if not TOKEN or not CHAT_ID:
        sys.exit("BOT_TOKEN and CHAT_ID must be set.")

    state = load_state()
    first_run = state is None
    state = state or {"seen": [], "last_error": None}
    seen = set(state["seen"])

    projects, errors, counts = [], [], {}
    for name, cfg in SOURCES.items():
        try:
            found = fetch_source(name, cfg)
            counts[name] = len(found)
            if not found:
                errors.append(f"{name}: قريت 0 مشروع، شكل الصفحة مختلف عن المتوقع")
            projects += found
        except Exception as exc:
            counts[name] = 0
            errors.append(f"{name}: {exc}")

    error_text = "\n".join(errors) if errors else None
    if error_text and error_text != state.get("last_error"):
        send("⚠️ مشكلة في قراءة المصادر:\n" + error_text)
    state["last_error"] = error_text

    new_matches = []
    for project in projects:
        if project["url"] in seen:
            continue
        seen.add(project["url"])
        if is_match(project):
            new_matches.append(project)

    if first_run:
        # First run only memorizes what exists now, so you do not get a flood of old projects.
        details = " | ".join(f"{n}: {c}" for n, c in counts.items())
        send(f"✅ البوت اشتغل على GitHub. المشاريع اللي اتقرت: {details}. هبعتلك الجديد بس من دلوقتي.")
    else:
        for p in new_matches[:MAX_PER_RUN]:
            budget = p["budget"] if p["budget"] is not None else "?"
            offers = p["offers"] if p["offers"] is not None else "?"
            send(
                "🎯 مشروع جديد مناسب!\n\n"
                f"📌 {p['title']}\n"
                f"💰 الميزانية: {budget}$\n"
                f"📊 العروض: {offers}\n"
                f"🌐 {p['source']}\n"
                f"🔗 {p['url']}"
            )

    state["seen"] = sorted(seen)[-2000:]
    save_state(state)
    print(f"projects={len(projects)} new_matches={len(new_matches)} errors={len(errors)}")


if __name__ == "__main__":
    main()
