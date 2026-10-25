#!/usr/bin/env python3
"""Curated Russian meme discovery. No blind image scraping or automatic publishing.

scan: fetch new article links from configured RSS/Atom feeds, plus seed references.
list: show pending sources.
approve ID /path/to/verified-image.jpg: review and import a local image.
reject ID: reject an irrelevant source.
"""
import argparse
import hashlib
import json
import shutil
import re
import gzip
from html.parser import HTMLParser
from html import unescape
from urllib.parse import urljoin
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "data" / "meme_candidates.json"
FEEDS = ROOT / "data" / "meme_feeds.txt"
MEDIA = ROOT / "assets" / "memes"
CATEGORIES = {"ex", "calls", "money", "travel", "general"}
DEFAULT_FEEDS = []
COMMUNITIES = [
    ("general", "https://pikabu.ru/community/Drugsmeme"),
    ("general", "https://pikabu.ru/community/blackhumour"),
    ("general", "https://pikabu.ru/community/cringe"),
]
KEYWORDS = re.compile(r"наркот|нарком|мефедрон|мефчик|солев(ой|ые|ик)|упорот|под веществ|психоактив|зависимост|передоз|ломк|трезвост|похмел|запой", re.I)

SEEDS = [
    ("general", "Мемариум — тематическая подборка про наркоманов", "https://memarium.fun/memy-pro/narkomanov/"),
    ("general", "JoyReactor — мемы про наркотики", "https://reactor.cc/search/Наркотики+мемы"),
    ("general", "Мэмисон — мемы про мефедрон и насвай", "https://t.me/s/memsearch?before=7758"),
    ("general", "Русский мем про соль и VPN", "https://ruanekdot.ru/news/slova/2026-08-20-89161"),
    ("general", "ооаоаа) ммм)) — постироничный наркоманский мем", "https://memepedia.ru/ooaoaa-mmm/"),
    ("general", "Ловите наркомана — крокодил Гена", "https://memepedia.ru/lovite-narkomana/"),
]
HEADERS = {"User-Agent": "DependenceTrackerCurator/2.0 (+https://github.com/D00PERDAP/DEPENDENCE-TRACKER)"}


def load():
    if INDEX.exists():
        return json.loads(INDEX.read_text(encoding="utf-8"))
    return []


def save(items):
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def feed_links(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read(2_000_000)
    root = ET.fromstring(data)
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if link.startswith("https://"):
            yield title, link
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for item in root.findall(".//atom:entry", ns):
        title = (item.findtext("atom:title", default="", namespaces=ns) or "").strip()
        for link in item.findall("atom:link", ns):
            href = link.attrib.get("href", "")
            if href.startswith("https://") and link.attrib.get("rel", "alternate") == "alternate":
                yield title, href
                break


class StoryLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None
        self.matches = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get("href", "")
        if tag == "a" and re.search(r"^(?:https://pikabu\.ru)?/story/[\w%-]+_\d+(?:[/?#]|$)", href):
            self.current = [urljoin("https://pikabu.ru", href).split("?")[0].split("#")[0], ""]
            self.matches += 1

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append(tuple(self.current))
            self.current = None


def community_links(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read(2_000_000)
        encoding = response.headers.get("Content-Encoding", "").lower()
        if encoding == "gzip":
            raw = gzip.decompress(raw)
        charset = response.headers.get_content_charset()
    if not charset:
        match = re.search(rb"<meta[^>]+charset=[\'\"]?([a-zA-Z0-9_-]+)", raw[:10000], re.I)
        charset = match.group(1).decode("ascii") if match else "utf-8"
    html = raw.decode(charset, errors="replace")
    parser = StoryLinks()
    parser.feed(html)
    print(f"Community {url}: matched anchors={parser.matches}, extracted={len(parser.links)}")
    return [(link, re.sub(r"\\s+", " ", title).strip()) for link, title in parser.links]


def scan():
    items = load()
    known = {x.get("page") for x in items}
    sources = list(SEEDS)
    configured = [f"{cat} {url}" for cat, url in DEFAULT_FEEDS]
    if FEEDS.exists():
        configured += FEEDS.read_text(encoding="utf-8").splitlines()
    for line in configured:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            category, url = line.split(maxsplit=1)
            if category not in CATEGORIES or not url.startswith("https://"):
                print("Skipped invalid feed:", line)
                continue
            for title, link in feed_links(url):
                if KEYWORDS.search(title):
                    sources.append((category, title, link))
        except (OSError, ValueError, ET.ParseError) as exc:
            print("Feed unavailable:", type(exc).__name__, line[:90])
    for category, url in COMMUNITIES:
        try:
            links = community_links(url)
            relevant = [(link, title.strip()) for link, title in links if title.strip() and KEYWORDS.search(title)]
            print(f"  Relevant titles: {len(relevant)}; sample titles: {[title[:65] for _, title in links[:3]]}")
            for link, title in relevant:
                sources.append((category, title, link))
        except (OSError, ValueError) as exc:
            print("Community unavailable:", type(exc).__name__, url)
    added = 0
    for category, title, link in sources:
        if link in known:
            continue
        items.append({"id": max((x["id"] for x in items), default=0) + 1,
                      "status": "pending", "category": category,
                      "title": title[:180], "page": link,
                      "found_at": datetime.now(timezone.utc).isoformat()})
        known.add(link)
        added += 1
    save(items)
    print(f"New source links: {added}. Pending: {sum(x['status']=='pending' for x in items)}.")
    print("Nothing is published until an image is manually reviewed and approved.")


def candidate(items, number):
    item = next((x for x in items if x["id"] == number), None)
    if item is None:
        raise SystemExit("No such ID")
    if item["status"] != "pending":
        raise SystemExit("Already reviewed")
    return item


def approve(number, filename, category):
    items = load()
    item = candidate(items, number)
    category = category or item["category"]
    if category not in CATEGORIES:
        raise SystemExit("Invalid category")
    source = Path(filename).expanduser().resolve()
    if not source.is_file() or source.stat().st_size > 10_000_000:
        raise SystemExit("Missing file or file over 10MB")
    header = source.read_bytes()[:12]
    ext = (".jpg" if header.startswith(bytes.fromhex("ffd8ff")) else
           ".png" if header.startswith(bytes.fromhex("89504e470d0a1a0a")) else
           ".webp" if header.startswith(b"RIFF") and header[8:12] == b"WEBP" else None)
    if ext is None:
        raise SystemExit("Only JPG/PNG/WEBP images are accepted")
    print(f"Source page: {item['page']}")
    print(f"Image: {source}")
    if input("Viewed image, confirmed relevance and permission to redistribute? Type YES: ").strip() != "YES":
        return
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    existing = list(MEDIA.rglob(digest + ".*")) if MEDIA.exists() else []
    if existing:
        raise SystemExit(f"Duplicate image: {existing[0]}")
    dest_dir = MEDIA / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (digest + ext)
    shutil.copyfile(source, dest)
    item.update({"status": "approved", "category": category,
                 "file": str(dest.relative_to(ROOT)), "sha256": digest})
    save(items)
    print("Added:", dest)



class ImageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property") in ("og:image", "twitter:image"):
            url = a.get("content", "")
            if url.startswith("https://") and url not in self.images:
                self.images.append(url)
        if tag == "img":
            url = a.get("data-src") or a.get("src") or ""
            if url.startswith("https://") and url not in self.images:
                self.images.append(url)


def preview(number):
    items = load()
    item = next((x for x in items if x["id"] == number), None)
    if not item:
        raise SystemExit("No such ID")
    print("Source:", item["page"])
    req = urllib.request.Request(item["page"], headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read(3_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
        parser = ImageMetadata()
        parser.feed(raw.decode(charset, errors="replace"))
        if not parser.images:
            print("No public image metadata found. Open source manually.")
        for image in parser.images[:12]:
            print("IMAGE:", unescape(image))
    except (OSError, ValueError) as exc:
        print("Preview unavailable:", type(exc).__name__, str(exc)[:160])



def sync_licensed(limit=8):
    """Import openly licensed, relevant Wikimedia Commons images only.

    Conservative by design: a filename match and verified free license are
    required; ambiguous material is not imported.
    """
    from urllib.parse import urlencode
    from PIL import Image, UnidentifiedImageError
    query = "наркоман meme"
    api = "https://commons.wikimedia.org/w/api.php?" + urlencode({
        "action": "query", "generator": "search", "gsrsearch": query,
        "gsrnamespace": 6, "gsrlimit": 50, "prop": "imageinfo",
        "iiprop": "url|extmetadata|size", "format": "json"
    })
    req = urllib.request.Request(api, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            pages = json.load(response).get("query", {}).get("pages", {})
    except (OSError, ValueError) as exc:
        print("Licensed source unavailable:", type(exc).__name__)
        return
    allowed = {"cc0", "public domain", "cc-by-4.0", "cc-by-sa-4.0",
               "cc-by-3.0", "cc-by-sa-3.0", "cc by 4.0", "cc by-sa 4.0",
               "cc by 3.0", "cc by-sa 3.0"}
    imported = 0
    rejected = 0
    manifest = ROOT / "data" / "licensed_memes.json"
    records = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    seen = {x.get("source") for x in records}
    for page in pages.values():
        title = page.get("title", "")
        if not KEYWORDS.search(title):
            rejected += 1
            continue
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        metadata = info.get("extmetadata") or {}
        license_name = re.sub(r"<[^>]+>", "", metadata.get("LicenseShortName", {}).get("value", "")).strip().lower()
        license_url = metadata.get("LicenseUrl", {}).get("value", "")
        source = info.get("descriptionurl", "")
        url = info.get("url", "")
        if not any(x == license_name for x in allowed) or not source.startswith("https://commons.wikimedia.org/") or source in seen:
            rejected += 1
            continue
        if not url.startswith("https://upload.wikimedia.org/") or info.get("size", 0) > 8_000_000:
            rejected += 1
            continue
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as response:
                data = response.read(8_000_001)
            if len(data) > 8_000_000:
                continue
            from io import BytesIO
            with Image.open(BytesIO(data)) as im:
                im.verify()
            with Image.open(BytesIO(data)) as im:
                if im.width < 250 or im.height < 150 or im.width * im.height > 18_000_000:
                    continue
                im = im.convert("RGB")
                digest = hashlib.sha256(im.tobytes()).hexdigest()
                target = MEDIA / "general" / (digest + ".jpg")
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                im.save(target, "JPEG", quality=88, optimize=True)
            records.append({"source": source, "file": str(target.relative_to(ROOT)),
                            "title": title, "license": license_name,
                            "license_url": license_url,
                            "author": re.sub(r"<[^>]+>", "", metadata.get("Artist", {}).get("value", ""))[:250]})
            seen.add(source)
            imported += 1
            if imported >= limit:
                break
        except (OSError, ValueError, UnidentifiedImageError):
            continue
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Imported licensed images: {imported}; rejected candidates: {rejected}.")
    if not imported:
        print("No verified relevant licensed images. Existing collection unchanged.")



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["scan", "list", "approve", "reject", "clean-old", "preview", "sync-licensed"])
    parser.add_argument("id", nargs="?", type=int)
    parser.add_argument("image", nargs="?")
    parser.add_argument("--category", choices=sorted(CATEGORIES))
    args = parser.parse_args()
    if args.action == "sync-licensed":
        sync_licensed()
    elif args.action == "scan":
        scan()
    elif args.action == "list":
        items = load()
        for item in items:
            if item["status"] == "pending":
                print(f"{item['id']} [{item['category']}] {item['title']}\n    {item['page']}")
        print("Pending:", sum(x["status"] == "pending" for x in items))
    elif args.action == "preview":
        if args.id is None:
            parser.error("Specify ID")
        preview(args.id)
    elif args.action == "clean-old":
        items = load()
        count = 0
        for item in items:
            if item["status"] == "pending" and "commons.wikimedia.org" in item.get("page", ""):
                item["status"] = "rejected"
                count += 1
        save(items)
        print("Removed irrelevant old Commons candidates:", count)
    else:
        if args.id is None:
            parser.error("Specify ID")
        if args.action == "reject":
            items = load()
            candidate(items, args.id)["status"] = "rejected"
            save(items)
            print("Rejected", args.id)
        else:
            if not args.image:
                parser.error("approve requires a local image path")
            approve(args.id, args.image, args.category)


if __name__ == "__main__":
    main()
