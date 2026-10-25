"""Automated image discovery and local review queue; no unlicensed auto-publication."""
import hashlib
import ipaddress
import socket
import io
import json
import urllib.error
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote, urlsplit, urlunsplit, urlencode, unquote
from PIL import Image, ImageOps, UnidentifiedImageError
from meme_review_store import read as read_review, transact as update_review

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
    "https://reactor.cc/search/мефедрон",
    "https://reactor.cc/search/Паша+Техник",
    "https://reactor.cc/search/солевые+мемы",
    "https://t.me/s/memsearch?before=7758",
]


def public_https(url):
    """Reject local IPs, local DNS results and HTTP destinations before access."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Public HTTPS URL required")
    if parsed.port not in (None, 443):
        raise ValueError("Nonstandard HTTPS port blocked")
    for record in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(record[4][0])
        if not address.is_global:
            raise ValueError("Non-public network target blocked")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        public_https(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch(url, limit):
    parts = urlsplit(url)
    url = urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%:@"),
                      quote(parts.query, safe="=&%:@+"), ""))
    public_https(url)
    request = urllib.request.Request(url, headers=HEADERS)
    opener = urllib.request.build_opener(SafeRedirect())
    with opener.open(request, timeout=15) as response:
        public_https(response.geturl())
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Response too large")
        return data, response.headers.get_content_type(), response.headers.get_content_charset() or "utf-8"


def topical_score(url, description="", source=""):
    """Cheap transparent heuristic, NOT image recognition or humour judgement."""
    hay = (unquote(urlsplit(url).path) + " " + description).lower()
    strong = re.compile(r"паша.?техник|техник|меф|наркот|нарком|солев|альфа.?пвп|мдма|mdma|lsd|лсд|амфет|кокаин|каннаб|марихуан|гашиш|героин|кетамин|упорот|трезв|ломк", re.I)
    score = 3 if strong.search(hay) else 1 if TOPIC.search(hay) else 0
    if strong.search(unquote(urlsplit(source).path)) and score:
        score += 1
    return score



class Images(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base = base
        self.urls = []
        self.context = {}
    def add(self, value, description=""):
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
        self.context[url] = (self.context.get(url, "") + " " + description)[:500]
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property", "").lower() in ("og:image", "twitter:image"):
            self.add(a.get("content"), a.get("alt", ""))
        if tag == "img":
            for name in ("data-original", "data-src", "src"):
                self.add(a.get(name), (a.get("alt", "") + " " + a.get("title", "")))
            if a.get("srcset"):
                self.add(a["srcset"].split(",")[-1].strip().split(" ")[0], a.get("alt", ""))


def fingerprint(image):
    small = ImageOps.grayscale(image).resize((9, 8))
    px = list(small.getdata())
    bits = [px[y * 9 + x] > px[y * 9 + x + 1] for y in range(8) for x in range(8)]
    return sum(int(bit) << i for i, bit in enumerate(bits))


def distance(a, b):
    return (int(a, 16) ^ int(b, 16)).bit_count()


def list_review():
    items = read_review()
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
    staged = []
    sources = list(SOURCES)
    try:
        from meme_collector import scan as scan_sources, load as load_sources
        scan_sources()
        sources.extend(x["page"] for x in load_sources()
                       if x.get("status") == "pending" and x.get("page", "").startswith("https://"))
    except (OSError, ValueError, UnicodeError) as exc:
        print("Source discovery unavailable:", type(exc).__name__)
    for source in dict.fromkeys(sources[:35]):
        if len(staged) >= 30:
            break
        try:
            html, mime, charset = fetch(source, MAX_HTML)
            if mime != "text/html":
                print("Skipped non-HTML source:", source)
                continue
            parser = Images(source)
            parser.feed(html.decode(charset, errors="replace"))
            print("Source:", source, "image links:", len(parser.urls))
            for url in parser.urls[:MAX_PER_SOURCE]:
                if len(staged) >= 30:
                    break
                if url in seen_urls:
                    continue
                score = topical_score(url, parser.context.get(url, ""), source)
                if score == 0:
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
                    staged.append({"status": "needs-rights-review", "source": source,
                                   "image_url": url, "preview": str(path.relative_to(ROOT)),
                                   "phash": phash, "relevance": score,
                                   "description": parser.context.get(url, "")[:280].strip(),
                                   "found_at": datetime.now(timezone.utc).isoformat()})
                    hashes.append(phash)
                    seen_urls.add(url)
                except (OSError, ValueError, UnidentifiedImageError) as exc:
                    print("  Skipped image:", type(exc).__name__)
        except (OSError, ValueError, UnicodeError) as exc:
            print("Source unavailable:", source, type(exc).__name__)
    def merge(latest):
        nonlocal added
        urls = {x.get("image_url") for x in latest}
        existing_hashes = [x.get("phash") for x in latest if x.get("phash")]
        next_id = max((x["id"] for x in latest), default=0)
        for item in staged:
            if item["image_url"] in urls or any(distance(item["phash"], h) <= 5 for h in existing_hashes):
                continue
            next_id += 1
            item["id"] = next_id
            latest.append(item)
            urls.add(item["image_url"])
            existing_hashes.append(item["phash"])
            added += 1
        return len(latest)

    total = update_review(merge)
    print(f"Review images added: {added}. Total queued: {total}.")
    print("Pinterest discovery is disabled without an authorized source/API.")
    print("Previews are NOT published. Source rights and actual meme relevance must be verified.")


if __name__ == "__main__":
    import sys
    list_review() if len(sys.argv) > 1 and sys.argv[1] == "list" else run()
