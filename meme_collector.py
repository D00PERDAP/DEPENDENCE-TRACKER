#!/usr/bin/env python3
"""Find real meme image candidates on Wikimedia Commons; human approval required.

Usage:
  python3 meme_collector.py scan
  python3 meme_collector.py list
  python3 meme_collector.py approve 3
  python3 meme_collector.py reject 3
Only approved items are downloaded to assets/memes/<category>.
"""
import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "data" / "meme_candidates.json"
MEDIA = ROOT / "assets" / "memes"
API = "https://commons.wikimedia.org/w/api.php"
SEARCHES = {
    "ex": ["ex girlfriend meme", "relationship meme"],
    "calls": ["phone call meme", "text message meme"],
    "money": ["money meme", "spending meme"],
    "travel": ["taxi meme", "driving meme"],
    "general": ["drug meme", "наркотики мем", "наркоман мем", "meth meme"],
}
ALLOWED_LICENSE = ("cc0", "public domain", "cc by", "cc-by", "cc-by-sa", "cc by-sa")
HEADERS = {"User-Agent": "DependenceTrackerMemeReview/1.0 (noncommercial review; contact via GitHub D00PERDAP/DEPENDENCE-TRACKER)"}


def request_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def load():
    if INDEX.exists():
        return json.loads(INDEX.read_text(encoding="utf-8"))
    return []


def save(rows):
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_html(value):
    return re.sub(r"<[^>]+>", "", value or "").strip()


def scan(limit=12):
    items = load()
    known = {x["page"] for x in items}
    added = 0
    for category, terms in SEARCHES.items():
        for term in terms:
            params = {
                "action": "query", "generator": "search", "gsrsearch": f'filetype:bitmap {term}',
                "gsrnamespace": 6, "gsrlimit": min(limit, 25), "prop": "imageinfo",
                "iiprop": "url|extmetadata|size|mime", "format": "json",
            }
            try:
                payload = request_json(API + "?" + urllib.parse.urlencode(params))
            except (OSError, ValueError) as exc:
                print(f"Search unavailable ({term}): {exc}")
                continue
            for page in payload.get("query", {}).get("pages", {}).values():
                info = (page.get("imageinfo") or [{}])[0]
                meta = info.get("extmetadata", {})
                license_name = clean_html(meta.get("LicenseShortName", {}).get("value", ""))
                usage = clean_html(meta.get("UsageTerms", {}).get("value", ""))
                if not any(k in (license_name + " " + usage).lower() for k in ALLOWED_LICENSE):
                    continue
                url = info.get("url", "")
                if not url.startswith("https://upload.wikimedia.org/") or not url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".webp")):
                    continue
                page_url = info.get("descriptionurl", "")
                if not page_url or page_url in known:
                    continue
                if info.get("size", 0) > 10_000_000:
                    continue
                items.append({
                    "id": max((x["id"] for x in items), default=0) + 1,
                    "status": "pending", "category": category, "title": page.get("title", ""),
                    "page": page_url, "url": url, "license": license_name,
                    "author": clean_html(meta.get("Artist", {}).get("value", ""))[:200],
                    "found_at": datetime.now(timezone.utc).isoformat(),
                })
                known.add(page_url)
                added += 1
    save(items)
    print(f"Added {added} candidates. Review them using: python3 meme_collector.py list")
    print("No images were published or sent to bot users.")


def review(action, item_id):
    rows = load()
    entry = next((x for x in rows if x["id"] == item_id), None)
    if not entry:
        raise SystemExit("Unknown candidate ID")
    if entry["status"] != "pending":
        raise SystemExit("Candidate already reviewed")
    if action == "reject":
        entry["status"] = "rejected"
    else:
        print(f"Source: {entry['page']}\nLicense: {entry['license']}\nAuthor: {entry['author']}")
        confirm = input("Have you viewed the image, checked its license, and confirmed it is suitable? Type YES: ")
        if confirm != "YES":
            print("Not approved.")
            return
        target = MEDIA / entry["category"]
        target.mkdir(parents=True, exist_ok=True)
        suffix = Path(urllib.parse.urlparse(entry["url"]).path).suffix.lower()
        file_name = hashlib.sha256(entry["url"].encode()).hexdigest()[:20] + suffix
        dest = target / file_name
        req = urllib.request.Request(entry["url"], headers=HEADERS)
        with urllib.request.urlopen(req, timeout=25) as response:
            data = response.read(10_000_001)
        if len(data) > 10_000_000:
            raise SystemExit("File too large")
        if not (data.startswith(b"\\xff\\xd8\\xff") or data.startswith(b"\\x89PNG\\r\\n\\x1a\\n") or data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
            raise SystemExit("Not a recognized image")
        dest.write_bytes(data)
        entry["status"] = "approved"
        entry["file"] = str(dest.relative_to(ROOT))
        print(f"Saved: {dest}")
    save(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("scan", "list", "approve", "reject"))
    parser.add_argument("id", nargs="?", type=int)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    if args.action == "scan":
        scan(args.limit)
    elif args.action == "list":
        for item in load():
            if item["status"] == "pending":
                print(f"{item['id']:>4} [{item['category']}] {item['title'][:65]}\n     {item['page']}\n     License: {item['license']}")
    else:
        if args.id is None:
            parser.error("Candidate ID required")
        review(args.action, args.id)


if __name__ == "__main__":
    main()
