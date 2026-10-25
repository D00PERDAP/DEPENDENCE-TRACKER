"""Automated image discovery and local review queue; no unlicensed auto-publication."""
import hashlib
import ipaddress
import socket
import io
import json
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote, urlsplit, urlunsplit, urlencode
from PIL import Image, ImageOps, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
REVIEW = ROOT / "data" / "meme_review"
INDEX = ROOT / "data" / "meme_review.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DependenceTracker/2.0)"}
MAX_HTML = 2_000_000
MAX_IMAGE = 6_000_000
MAX_PER_SOURCE = 12
ALLOWED = {"image/jpeg", "image/png", "image/webp"}
BLOCK = ("avatar", "logo", "icon", "emoji", "favicon", "banner", "pixel", "tracking", "ads")
TOPIC = re.compile(r"наркот|нарком|мефедрон|меф|соль|соли|амфетамин|кокаин|героин|каннабис|марихуан|гашиш|мдма|mdma|lsd|лсд|кетамин|психоделик|упорот|прикол|мем", re.I)
PINTEREST_TERMS = (
    "меф мемы", "мефедрон приколы", "Паша Техник мемы", "Паша Техник цитаты",
    "наркоманские мемы", "упоротые мемы", "солевые мемы", "альфа пвп мемы",
    "амфетамин мемы", "скорость мемы", "экстази мемы", "MDMA мемы",
    "марихуана мемы", "трава мемы", "гашиш мемы", "кокаин мемы",
    "ЛСД мемы", "психоделики мемы", "кетамин мемы", "героин мемы",
    "наркотики черный юмор", "трезвость мемы", "ломка мемы",
)
PINTEREST_SEARCHES = [
    "https://www.pinterest.com/search/pins/?" + urlencode({"q": term})
    for term in PINTEREST_TERMS
]
SOURCES = [
    "https://memarium.fun/memy-pro/narkomanov/",
    "https://memepedia.ru/ooaoaa-mmm/",
    "https://memepedia.ru/lovite-narkomana/",
    "https://reactor.cc/search/Наркотики+мемы",
    "https://t.me/s/memsearch?before=7758",

]


def fetch(url, limit):
    parts = urlsplit(url)
    url = urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%:@"), quote(parts.query, safe="=&%:@+"), ""))
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("HTTPS URL required")
    for record in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(record[4][0])
        if not address.is_global:
            raise ValueError("Non-public network target blocked")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as response:
        if response.geturl().split(":")[0] != "https":
            raise ValueError("Redirected outside HTTPS")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Response too large")
        return data, response.headers.get_content_type(), response.headers.get_content_charset() or "utf-8"


class Images(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base = base
        self.urls = []
    def add(self, value):
        if not value:
            return
        url = urljoin(self.base, value.strip()).split("#")[0]
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return
        if any(b in parsed.path.lower() for b in BLOCK):
            return
        if url not in self.urls:
            self.urls.append(url)
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property", "").lower() in ("og:image", "twitter:image"):
            self.add(a.get("content"))
        if tag == "img":
            for name in ("data-original", "data-src", "src"):
                self.add(a.get(name))
            if a.get("srcset"):
                self.add(a["srcset"].split(",")[-1].strip().split(" ")[0])


def fingerprint(image):
    small = ImageOps.grayscale(image).resize((9, 8))
    px = list(small.getdata())
    bits = [px[y * 9 + x] > px[y * 9 + x + 1] for y in range(8) for x in range(8)]
    return sum(int(bit) << i for i, bit in enumerate(bits))


def distance(a, b):
    return (int(a, 16) ^ int(b, 16)).bit_count()


def list_review():
    items = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else []
    for item in items:
        print(f"#{item['id']} [{item['status']}] {item['preview']}")
        print(" Source:", item["source"])
        print(" Image:", item["image_url"])
    print("Total:", len(items))


def run():
    REVIEW.mkdir(parents=True, exist_ok=True)
    items = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else []
    seen_urls = {item["image_url"] for item in items}
    hashes = [item["phash"] for item in items if item.get("phash")]
    added = 0
    for source in SOURCES:
        try:
            html, mime, charset = fetch(source, MAX_HTML)
            if mime != "text/html":
                print("Skipped non-HTML source:", source)
                continue
            parser = Images(source)
            parser.feed(html.decode(charset, errors="replace"))
            print("Source:", source, "image links:", len(parser.urls))
            if source in PINTEREST_SEARCHES and not parser.urls:
                print("Pinterest search has no accessible image metadata; no bypass attempted.")
            for url in parser.urls[:MAX_PER_SOURCE]:
                if url in seen_urls:
                    continue
                # Reject unrelated image filenames, not merely pages with matching search terms.
                if source not in PINTEREST_SEARCHES and not TOPIC.search(urlparse(url).path):
                    continue
                try:
                    data, content_type, _ = fetch(url, MAX_IMAGE)
                    if content_type not in ALLOWED:
                        continue
                    with Image.open(io.BytesIO(data)) as image:
                        image.verify()
                    with Image.open(io.BytesIO(data)) as image:
                        if image.width < 300 or image.height < 200 or image.width * image.height > 20_000_000:
                            continue
                        phash = format(fingerprint(image.convert("RGB")), "016x")
                        if any(distance(phash, h) <= 5 for h in hashes):
                            continue
                        thumbnail = ImageOps.exif_transpose(image).convert("RGB")
                        thumbnail.thumbnail((1000, 1000))
                        digest = hashlib.sha256(data).hexdigest()
                        path = REVIEW / (digest + ".jpg")
                        thumbnail.save(path, "JPEG", quality=85)
                    items.append({"id": max((x["id"] for x in items), default=0) + 1,
                                  "status": "needs-rights-review", "source": source,
                                  "image_url": url, "preview": str(path.relative_to(ROOT)),
                                  "phash": phash, "found_at": datetime.now(timezone.utc).isoformat()})
                    hashes.append(phash)
                    seen_urls.add(url)
                    added += 1
                except (OSError, ValueError, UnidentifiedImageError) as exc:
                    print("  Skipped image:", type(exc).__name__)
        except (OSError, ValueError, UnicodeError) as exc:
            print("Source unavailable:", source, type(exc).__name__)
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(INDEX)
    print(f"Review images added: {added}. Total queued: {len(items)}.")
    print("Pinterest search terms configured:", len(PINTEREST_TERMS), "(no unauthorized Pinterest scraping).")
    print("Previews are NOT published. Source rights and actual meme relevance must be verified.")


if __name__ == "__main__":
    import sys
    list_review() if len(sys.argv) > 1 and sys.argv[1] == "list" else run()
