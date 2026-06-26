#!/usr/bin/env python3
"""
AnimeSalt.ac → Supabase Scraper Pipeline
Fetches ALL anime, cartoons, movies from every category/network/language page.
Run:  python3 pipeline.py
Stop anytime with Ctrl+C — safe to resume; already-scraped content is skipped/updated.
"""

import os
import re
import sys
import time
import random
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urljoin, urlparse, urlunparse, urlencode, parse_qs, urlencode
from typing import Optional

import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

# ── ANSI colors ───────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
BLUE   = "\033[34m"
CYAN   = "\033[36m"
RED    = "\033[31m"
MAGENTA= "\033[35m"
WHITE  = "\033[37m"

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def con(color: str, symbol: str, msg: str, indent: int = 0):
    pad = "  " * indent
    print(f"{DIM}{ts()}{RESET} {pad}{color}{BOLD}{symbol}{RESET} {msg}", flush=True)

def con_ok(msg: str, indent: int = 0):      con(GREEN,   "✓", msg, indent)
def con_new(msg: str, indent: int = 0):     con(CYAN,    "+", msg, indent)
def con_upd(msg: str, indent: int = 0):     con(BLUE,    "↑", msg, indent)
def con_skip(msg: str, indent: int = 0):    con(DIM,     "–", msg, indent)
def con_warn(msg: str, indent: int = 0):    con(YELLOW,  "⚠", msg, indent)
def con_err(msg: str, indent: int = 0):     con(RED,     "✗", msg, indent)
def con_head(msg: str):
    print(f"\n{MAGENTA}{BOLD}{'━'*60}{RESET}\n{MAGENTA}{BOLD}  {msg}{RESET}\n{MAGENTA}{BOLD}{'━'*60}{RESET}", flush=True)
def con_sub(msg: str):
    print(f"\n{BLUE}{BOLD}  ▸ {msg}{RESET}", flush=True)
def con_progress(current: int, total: int, title: str):
    pct = int(current / total * 40) if total else 0
    bar = f"[{GREEN}{'█'*pct}{DIM}{'░'*(40-pct)}{RESET}]"
    print(f"\r{DIM}{ts()}{RESET} {bar} {CYAN}{current}/{total}{RESET} {title[:50]}", end="", flush=True)
def con_progress_done():
    print(flush=True)

# ── Stats tracker ─────────────────────────────────────────────────────────────
class Stats:
    def __init__(self):
        self.content_new = 0
        self.content_updated = 0
        self.content_skipped = 0
        self.episodes_new = 0
        self.episodes_updated = 0
        self.servers_new = 0
        self.images_fixed = 0
        self.errors = 0
        self.start = time.time()

    def report(self):
        elapsed = int(time.time() - self.start)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        con_head("SCRAPE COMPLETE — Final Report")
        print(f"  {GREEN}Content added   : {BOLD}{self.content_new}{RESET}")
        print(f"  {BLUE}Content updated : {BOLD}{self.content_updated}{RESET}")
        print(f"  {DIM}Content skipped : {self.content_skipped}{RESET}")
        print(f"  {CYAN}Episodes added  : {BOLD}{self.episodes_new}{RESET}")
        print(f"  {BLUE}Episodes updated: {BOLD}{self.episodes_updated}{RESET}")
        print(f"  {CYAN}Servers stored  : {BOLD}{self.servers_new}{RESET}")
        print(f"  {YELLOW}Images fixed    : {BOLD}{self.images_fixed}{RESET}")
        print(f"  {RED}Errors          : {BOLD}{self.errors}{RESET}")
        print(f"  {DIM}Time elapsed    : {h:02d}:{m:02d}:{s:02d}{RESET}\n")

STATS = Stats()

# ── Config ──────────────────────────────────────────────────────────────────
BASE_URL       = "https://animesalt.ac"
SITEMAP_INDEX  = "https://animesalt.ac/wp-sitemap.xml"
LOGO_URLS      = {
    "http://animesalt.ac/wp-content/uploads/AnimeSaltLong.png",
    "https://animesalt.ac/wp-content/uploads/AnimeSaltLong.png",
    "http://animesalt.ac/wp-content/uploads/AnimeSaltLong-1.png",
    "https://animesalt.ac/wp-content/uploads/AnimeSaltLong-1.png",
}
EXCLUDE_PATH_PATTERNS = [
    "AnimeSaltLong", "cropped-AnimeSalt",
    "sonyay", "sony-yay",
]
EXCLUDE_FILENAME_PATTERNS = [
    "favicon", "watermark",
]
REQUEST_DELAY  = (1.5, 3.0)
MAX_RETRIES    = 3

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# ── Logging ──────────────────────────────────────────────────────────────────
_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraper.log")
try:
    _file_handler = logging.FileHandler(_log_path, encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
except OSError:
    _file_handler = logging.NullHandler()
log = logging.getLogger("animesalt")
log.setLevel(logging.DEBUG)
log.addHandler(_file_handler)
log.propagate = False

# ── HTTP Session ─────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE_URL,
})


def fetch(url: str, retries: int = MAX_RETRIES) -> Optional[str]:
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(*REQUEST_DELAY))
            r = session.get(url, timeout=25)
            if r.status_code == 200:
                return r.text
            elif r.status_code == 404:
                log.warning(f"404 {url}")
                return None
            else:
                log.warning(f"HTTP {r.status_code} {url} (attempt {attempt+1})")
        except Exception as e:
            log.warning(f"Error fetching {url}: {e} (attempt {attempt+1})")
        time.sleep(2 ** attempt)
    return None


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ── Image helpers ─────────────────────────────────────────────────────────────
def is_logo(url: Optional[str]) -> bool:
    if not url:
        return True
    if url in LOGO_URLS:
        return True
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    for pattern in EXCLUDE_PATH_PATTERNS:
        if pattern.lower() in path_lower:
            return True
    filename = path_lower.split("/")[-1]
    for pattern in EXCLUDE_FILENAME_PATTERNS:
        if pattern.lower() in filename:
            return True
    return False


def best_img(*candidates) -> Optional[str]:
    for c in candidates:
        if c and not is_logo(c) and c.startswith("http"):
            return c.strip()
    return None


def normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def extract_image(tag) -> Optional[str]:
    if tag is None:
        return None
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        val = tag.get(attr)
        if val and not val.startswith("data:") and not is_logo(val):
            return normalize_url(val)
    return None


# ── URL classification ─────────────────────────────────────────────────────
def classify_url(url: str) -> Optional[str]:
    p = urlparse(url).path
    if p.startswith("/series/"):
        return "series"
    if p.startswith("/movies/"):
        return "movie"
    if p.startswith("/episode/"):
        return "episode"
    return None


def canonical_content_url(url: str) -> str:
    """Strip query/fragment from content URLs for deduplication."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


# ── Sitemap discovery ─────────────────────────────────────────────────────────
def get_sitemap_urls(sitemap_url: str) -> list[str]:
    log.info(f"Fetching sitemap: {sitemap_url}")
    html = fetch(sitemap_url)
    if not html:
        return []
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    try:
        root = ET.fromstring(html)
    except ET.ParseError:
        log.error("Could not parse sitemap XML")
        return []
    sub_maps = root.findall("sm:sitemap/sm:loc", ns)
    if sub_maps:
        urls = []
        for sm in sub_maps:
            urls.extend(get_sitemap_urls(sm.text.strip()))
        return urls
    return [u.text.strip() for u in root.findall("sm:url/sm:loc", ns)]


# ── Category seed list ────────────────────────────────────────────────────────
# Base category seeds — every entry is crawled once for series AND once with ?type=movies
BASE_CATEGORY_SEEDS = [
    "/series/",
    "/movies/",
    "/category/anime/",
    "/category/cartoon/",
    "/category/anime-movie/",
    "/category/dubbed/",
    "/category/subbed/",
    "/category/ongoing/",
    "/category/completed/",
    # Networks
    "/category/network/cartoon-network/",
    "/category/network/disney-channel/",
    "/category/network/disney-plus/",
    "/category/network/nickelodeon/",
    "/category/network/netflix/",
    "/category/network/amazon-prime/",
    "/category/network/hulu/",
    "/category/network/crunchyroll/",
    "/category/network/funimation/",
    "/category/network/toonami/",
    "/category/network/pogo/",
    "/category/network/sony-yay/",
    "/category/network/hungama-tv/",
    "/category/network/vh1/",
    "/category/network/zee-tv/",
    "/category/network/star-plus/",
    "/category/network/colors-tv/",
    "/category/network/hbo/",
    "/category/network/hbo-max/",
    "/category/network/adult-swim/",
    "/category/network/boomerang/",
    "/category/network/bbc/",
    "/category/network/fox/",
    "/category/network/paramount/",
    # Languages
    "/category/language/hindi/",
    "/category/language/english/",
    "/category/language/japanese/",
    "/category/language/tamil/",
    "/category/language/telugu/",
    "/category/language/bengali/",
    "/category/language/malayalam/",
    "/category/language/kannada/",
    "/category/language/marathi/",
    "/category/language/gujarati/",
    "/category/language/punjabi/",
    # Genres
    "/genre/action/",
    "/genre/adventure/",
    "/genre/comedy/",
    "/genre/drama/",
    "/genre/fantasy/",
    "/genre/horror/",
    "/genre/romance/",
    "/genre/sci-fi/",
    "/genre/slice-of-life/",
    "/genre/sports/",
    "/genre/supernatural/",
    "/genre/thriller/",
    "/genre/mystery/",
    "/genre/mecha/",
    "/genre/school/",
    "/genre/shounen/",
    "/genre/shoujo/",
    "/genre/seinen/",
    "/genre/josei/",
    "/genre/isekai/",
    "/genre/music/",
    "/genre/historical/",
    "/genre/military/",
    "/genre/magic/",
    "/genre/harem/",
    "/genre/ecchi/",
    "/genre/psychological/",
    "/genre/game/",
    "/genre/super-power/",
    "/genre/vampire/",
    "/genre/demons/",
    "/genre/kids/",
    "/genre/parody/",
    "/genre/space/",
    "/genre/cars/",
    "/genre/martial-arts/",
    "/genre/samurai/",
    "/genre/police/",
    "/genre/dementia/",
    "/genre/animation/",
    "/genre/family/",
]


def slugify(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def guess_content_urls(title: str, content_type: str) -> list[str]:
    base = "series" if content_type == "series" else "movies"
    variants = [title]
    trimmed = re.sub(r"\s+(season\s*\d+|\(\d{4}\)|part\s*\d+).*$", "", title, flags=re.IGNORECASE).strip()
    if trimmed != title:
        variants.append(trimmed)
    urls = []
    seen_slugs: set[str] = set()
    for v in variants:
        slug = slugify(v)
        if slug and slug not in seen_slugs:
            seen_slugs.add(slug)
            urls.append(f"{BASE_URL}/{base}/{slug}/")
        slug2 = slugify(v.replace(":", "").replace("'", ""))
        if slug2 and slug2 not in seen_slugs:
            seen_slugs.add(slug2)
            urls.append(f"{BASE_URL}/{base}/{slug2}/")
    return urls


def autodiscover_taxonomy_seeds(homepage_html: Optional[str]) -> list[str]:
    """
    Auto-discover all category/genre/network/language slugs
    by parsing the homepage and every nav/menu link on the site.
    Returns a list of path strings (with trailing slash).
    """
    seeds: list[str] = []
    if not homepage_html:
        return seeds
    s = soup(homepage_html)
    for a in s.select("a[href]"):
        href = str(a.get("href", "")).strip()
        try:
            parsed = urlparse(href)
        except Exception:
            continue
        # Only same-domain links
        if parsed.netloc and "animesalt.ac" not in parsed.netloc:
            continue
        path = parsed.path
        if re.match(r"^/(category|genre|tag)/[^/]+(/[^/]+)?/?$", path):
            clean = path if path.endswith("/") else path + "/"
            seeds.append(clean)
    return list(dict.fromkeys(seeds))


def crawl_listing(path_or_url: str) -> list[str]:
    """
    Crawl a listing page (with optional query string) through all pagination.
    Returns deduplicated list of /series/ and /movies/ URLs found.
    """
    found: list[str] = []
    seen: set[str] = set()

    # Determine base URL and existing query params
    if path_or_url.startswith("http"):
        base_listing_url = path_or_url
    else:
        base_listing_url = urljoin(BASE_URL, path_or_url)

    parsed_base = urlparse(base_listing_url)
    base_qs = parsed_base.query  # e.g. "type=movies"

    page = 1
    while True:
        # Build paginated URL: insert /page/N/ before query string
        path = parsed_base.path.rstrip("/")
        if page == 1:
            page_path = parsed_base.path
        else:
            page_path = f"{path}/page/{page}/"

        page_url = urlunparse((
            parsed_base.scheme,
            parsed_base.netloc,
            page_path,
            "",
            base_qs,
            ""
        ))

        html = fetch(page_url)
        if not html:
            break

        s = soup(html)
        links = s.select("a[href]")
        page_found = []
        for a in links:
            href = str(a.get("href", ""))
            ct = classify_url(href)
            if ct in ("series", "movie"):
                clean = canonical_content_url(href)
                if clean not in seen:
                    seen.add(clean)
                    page_found.append(clean)

        if not page_found:
            break

        found.extend(page_found)
        log.info(f"  Listing {page_url}: found {len(page_found)} items (total {len(found)})")

        # Check for next page
        nxt = s.select_one("a.next, a[rel=next], .nav-previous a, .pagination a[href*='page']")
        if not nxt:
            # Also check if there's a page N+1 link in pagination
            page_links = s.select(".pagination a[href], .page-numbers a[href]")
            has_next_page = any(f"/page/{page+1}/" in str(a.get("href", "")) for a in page_links)
            if not has_next_page:
                break

        page += 1
        if page > 500:
            break

    return list(dict.fromkeys(found))


# ── Content page scraper ──────────────────────────────────────────────────────
def parse_content_page(url: str, content_type: str) -> Optional[dict]:
    html = fetch(url)
    if not html:
        return None
    s = soup(html)

    title = (
        (s.find("h1") or s.find("h2"))
        and (s.find("h1") or s.find("h2")).get_text(strip=True)
    )
    if not title:
        title_tag = s.find("meta", property="og:title")
        title = title_tag["content"].strip() if title_tag else url.split("/")[-2]
    title = re.sub(r"\s*[-|]\s*Anime Salt.*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s*[-|]\s*Watch Now.*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s*[-|]\s*AnimeSalt.*$", "", title, flags=re.IGNORECASE).strip()

    desc_tag = s.find("meta", attrs={"name": "description"})
    og_desc  = s.find("meta", property="og:description")
    overview_el = s.select_one(".overview, .description, .sinopse, [class*='overview'], [class*='sinopse']")
    description = None
    if overview_el:
        description = overview_el.get_text(separator=" ", strip=True)
    elif og_desc and og_desc.get("content"):
        description = og_desc["content"].strip()
    elif desc_tag and desc_tag.get("content"):
        description = desc_tag["content"].strip()

    og_img = s.find("meta", property="og:image")
    og_img_url = normalize_url(og_img["content"].strip()) if og_img and og_img.get("content") else None
    og_valid = og_img_url if not is_logo(og_img_url) else None

    all_imgs = s.select("img[src], img[data-src], img[data-lazy-src], img[data-original]")
    tmdb_images: list[str] = []
    cdn_images:  list[str] = []
    wp_images:   list[str] = []
    all_real:    list[str] = []

    for img in all_imgs:
        v = extract_image(img)
        if not v or is_logo(v):
            continue
        all_real.append(v)
        if "tmdb.org" in v or "image.tmdb" in v:
            tmdb_images.append(v)
        elif "img.animesalt.ac" in v:
            cdn_images.append(v)
        elif "animesalt.ac" in v and "wp-content" in v:
            wp_images.append(v)

    bg_images: list[str] = []
    for el in s.select("[style*='background']"):
        style = el.get("style", "")
        m = re.search(r"url\(['\"]?(https?://[^'\")\s]+)['\"]?\)", style)
        if m:
            u = m.group(1)
            if not is_logo(u):
                bg_images.append(u)

    poster_el = s.select_one(
        ".poster img, .image-poster img, .capa img, "
        "[class*='poster'] img, .sidebar img, .cover img, "
        ".thumb img, .entry-thumbnail img"
    )
    banner_el = s.select_one(
        ".backdrop img, .banner img, .hero img, .wp-post-image, "
        ".featured-image img, .post-thumbnail img, "
        ".entry-header img, #banner img"
    )
    poster_from_el = extract_image(poster_el)
    banner_from_el = extract_image(banner_el)

    thumbnail_url = best_img(og_valid, *cdn_images, *wp_images, poster_from_el, *tmdb_images, *bg_images, all_real[0] if all_real else None)
    poster_url    = best_img(poster_from_el, *tmdb_images, og_valid, *cdn_images, *wp_images, all_real[0] if all_real else None)
    banner_url    = best_img(banner_from_el, *bg_images, *cdn_images, *wp_images, og_valid, *tmdb_images, all_real[0] if all_real else None)

    any_img = best_img(thumbnail_url, poster_url, banner_url, og_valid, *all_real)
    if not thumbnail_url: thumbnail_url = any_img
    if not poster_url:    poster_url    = any_img
    if not banner_url:    banner_url    = any_img

    year = None
    year_el = s.select_one(".year, [class*='year'], .date, time")
    if year_el:
        m = re.search(r"\b(19|20)\d{2}\b", year_el.get_text())
        if m:
            year = int(m.group())
    if not year:
        pub = s.find("meta", property="article:published_time")
        if pub and pub.get("content"):
            m = re.search(r"(20\d{2})", pub["content"])
            if m:
                year = int(m.group())

    duration_minutes = None
    dur_el = s.select_one(".runtime, .duration, [class*='runtime'], [class*='duration']")
    if dur_el:
        dur_txt = dur_el.get_text()
        m = re.search(r"(\d+)\s*h(?:r|our)?s?\s*(\d+)?\s*m", dur_txt, re.IGNORECASE)
        if m:
            h = int(m.group(1))
            mn = int(m.group(2) or 0)
            duration_minutes = h * 60 + mn
        else:
            m2 = re.search(r"(\d+)\s*min", dur_txt, re.IGNORECASE)
            if m2:
                duration_minutes = int(m2.group(1))

    genres = []
    genre_links = s.select("a[href*='/category/genre/'], a[href*='/genre/'], .genres a, .categorias a, [class*='genre'] a")
    for a in genre_links:
        g = a.get_text(strip=True)
        if g and len(g) < 40:
            genres.append(g)
    genres = list(dict.fromkeys(genres))

    languages = []
    lang_links = s.select("a[href*='/category/language/'], .languages a, [class*='language'] a")
    for a in lang_links:
        l = a.get_text(strip=True)
        if l and len(l) < 30:
            languages.append(l)
    languages = list(dict.fromkeys(languages))
    primary_language = languages[0] if languages else "Japanese"

    # Detect networks/studios from page
    networks = []
    network_links = s.select("a[href*='/category/network/'], .network a, [class*='network'] a")
    for a in network_links:
        n = a.get_text(strip=True)
        if n and len(n) < 60:
            networks.append(n)
    networks = list(dict.fromkeys(networks))

    episodes = []
    if content_type == "series":
        episodes = parse_episode_list(s, url)

    status = "completed" if content_type == "movie" else "ongoing"
    status_el = s.select_one(".status, [class*='status']")
    if status_el:
        st = status_el.get_text(strip=True).lower()
        if "complet" in st or "finished" in st:
            status = "completed"
        elif "ongoing" in st or "airing" in st:
            status = "ongoing"

    rating = 0.0
    rating_el = s.select_one(".rating, .score, [class*='rating'], [class*='score']")
    if rating_el:
        m = re.search(r"(\d+\.?\d*)", rating_el.get_text())
        if m:
            rating = float(m.group(1))

    slug = urlparse(url).path.strip("/").split("/")[-1]

    return {
        "title": title,
        "description": description,
        "type": content_type,
        "release_year": year,
        "rating": round(min(rating, 10.0), 1),
        "poster_url": poster_url,
        "banner_url": banner_url,
        "thumbnail_url": thumbnail_url,
        "duration_minutes": duration_minutes,
        "language": primary_language,
        "status": status,
        "featured": False,
        "_genres": genres,
        "_languages": languages,
        "_networks": networks,
        "_episodes": episodes,
        "_source_url": url,
        "_slug": slug,
    }


def parse_episode_list(s: BeautifulSoup, series_url: str) -> list[dict]:
    episodes = []
    seen_keys: set[tuple] = set()

    for ep_link in s.select("a[href*='/episode/']"):
        href = ep_link.get("href", "")
        if not href:
            continue
        ep_text = ep_link.get_text(strip=True)

        s_num, e_num = 1, 1
        # Try SxEx or S01E01 patterns
        m = re.search(r"[Ss](?:eason)?\s*(\d+)[Ee](?:p(?:isode)?)?\s*(\d+)", ep_text or href)
        if m:
            s_num, e_num = int(m.group(1)), int(m.group(2))
        else:
            # Try "1x12" pattern
            m2 = re.search(r"(\d+)x(\d+)", ep_text or href)
            if m2:
                s_num, e_num = int(m2.group(1)), int(m2.group(2))
            else:
                m3 = re.search(r"[Ee](?:p(?:isode)?)?\s*(\d+)", ep_text or href)
                if m3:
                    e_num = int(m3.group(1))
                else:
                    # Try extracting episode number from slug
                    slug_part = href.rstrip("/").split("/")[-1]
                    m4 = re.search(r"-(\d+)x(\d+)$", slug_part)
                    if m4:
                        s_num, e_num = int(m4.group(1)), int(m4.group(2))
                    else:
                        m5 = re.search(r"-ep?-?(\d+)$", slug_part, re.IGNORECASE)
                        if m5:
                            e_num = int(m5.group(1))

        key = (s_num, e_num)
        if key not in seen_keys:
            seen_keys.add(key)
            episodes.append({
                "season_number": s_num,
                "episode_number": e_num,
                "title": ep_text or f"Episode {e_num}",
                "url": href,
                "thumbnail_url": None,
                "duration_seconds": None,
            })

    episodes.sort(key=lambda e: (e["season_number"], e["episode_number"]))
    return episodes


def parse_episode_page(url: str) -> dict:
    html = fetch(url)
    if not html:
        return {}
    s = soup(html)
    video_servers = []

    # Direct iframes
    for iframe in s.select("iframe[src], iframe[data-src]"):
        src = iframe.get("src") or iframe.get("data-src") or ""
        src = src.strip()
        if src and src.startswith("http"):
            video_servers.append({
                "server_name": urlparse(src).netloc or "EMBED",
                "stream_url": src,
                "quality": "1080p",
                "language": "Japanese",
            })

    # Also look for JS-embedded sources
    for script in s.select("script"):
        script_text = script.get_text()
        for src_match in re.finditer(r"(?:file|src|source)\s*:\s*['\"]?(https?://[^'\">\s,]+)", script_text):
            src = src_match.group(1)
            if src not in {v["stream_url"] for v in video_servers}:
                video_servers.append({
                    "server_name": urlparse(src).netloc or "EMBED",
                    "stream_url": src,
                    "quality": "1080p",
                    "language": "Japanese",
                })

    thumb_el = s.select_one(".episode-thumbnail img, .thumb img, .episode-image img")
    thumb = extract_image(thumb_el)
    og_img = s.find("meta", property="og:image")
    og = normalize_url(og_img["content"].strip()) if og_img and og_img.get("content") else None
    thumbnail_url = best_img(thumb, og)
    title_el = s.find("h1") or s.find("h2")
    title = title_el.get_text(strip=True) if title_el else None
    if title:
        title = re.sub(r"\s*[-|]\s*Anime Salt.*$", "", title, flags=re.IGNORECASE).strip()
        title = re.sub(r"\s*[-|]\s*AnimeSalt.*$", "", title, flags=re.IGNORECASE).strip()
    return {"thumbnail_url": thumbnail_url, "title": title, "video_servers": video_servers}


# ── DB helpers ────────────────────────────────────────────────────────────────
def get_or_create_genre(db: Client, name: str) -> Optional[str]:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    try:
        res = db.table("genres").select("id").eq("slug", slug).execute()
        if res.data:
            return res.data[0]["id"]
        ins = db.table("genres").insert({"name": name, "slug": slug}).execute()
        return ins.data[0]["id"]
    except Exception as e:
        log.warning(f"Genre '{name}': {e}")
        return None


def find_content_by_title(db: Client, title: str) -> Optional[dict]:
    try:
        res = db.table("content").select(
            "id, title, poster_url, thumbnail_url, banner_url, description, release_year, language, status"
        ).eq("title", title).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        log.warning(f"find_content error: {e}")
        return None


def upsert_content(db: Client, data: dict) -> Optional[str]:
    genres    = data.pop("_genres", [])
    data.pop("_languages", None)
    data.pop("_networks", None)
    data.pop("_episodes", None)
    data.pop("_source_url", None)
    data.pop("_slug", None)

    title = data.get("title", "?")
    existing = find_content_by_title(db, title)

    try:
        if existing:
            content_id = existing["id"]
            updates = {}
            for field in ("poster_url", "banner_url", "thumbnail_url"):
                new_val = data.get(field)
                old_val = existing.get(field)
                if new_val and not is_logo(new_val):
                    if not old_val or is_logo(old_val) or old_val != new_val:
                        updates[field] = new_val
            for field in ("description", "release_year", "language", "status"):
                if data.get(field) and not existing.get(field):
                    updates[field] = data[field]
            if updates:
                db.table("content").update(updates).eq("id", content_id).execute()
                STATS.content_updated += 1
                con_upd(f"[UPDATED] {title} — {', '.join(updates.keys())}", indent=1)
            else:
                STATS.content_skipped += 1
                con_skip(f"[SKIP] {title} — already complete", indent=1)
        else:
            res = db.table("content").insert(data).execute()
            content_id = res.data[0]["id"]
            STATS.content_new += 1
            thumb_ok = "🖼" if data.get("thumbnail_url") and not is_logo(data.get("thumbnail_url", "")) else "❌"
            poster_ok = "🖼" if data.get("poster_url") and not is_logo(data.get("poster_url", "")) else "❌"
            con_new(f"[NEW] {title} | thumb:{thumb_ok} poster:{poster_ok} | {data.get('type','?')} {data.get('release_year','')}".strip(), indent=1)

        # Link genres
        for g_name in genres:
            g_id = get_or_create_genre(db, g_name)
            if g_id:
                try:
                    db.table("content_genres").upsert(
                        {"content_id": content_id, "genre_id": g_id},
                        on_conflict="content_id,genre_id"
                    ).execute()
                except Exception as e:
                    log.warning(f"content_genres link error: {e}")

        return content_id
    except Exception as e:
        log.error(f"upsert_content error '{title}': {e}", exc_info=True)
        STATS.errors += 1
        con_err(f"[ERROR] {title}: {e}", indent=1)
        return None


def get_existing_episodes(db: Client, content_id: str) -> dict[tuple[int, int], str]:
    try:
        res = db.table("episodes").select("id, season_number, episode_number").eq("content_id", content_id).execute()
        return {(r["season_number"], r["episode_number"]): r["id"] for r in res.data}
    except Exception:
        return {}


def upsert_episode(db: Client, content_id: str, ep: dict, existing_id: Optional[str]) -> Optional[str]:
    s_num = ep.get("season_number", 1)
    e_num = ep.get("episode_number", 1)
    ep_label = f"S{s_num}E{e_num}"
    row = {
        "content_id": content_id,
        "season_number": s_num,
        "episode_number": e_num,
        "title": ep.get("title") or f"Episode {e_num}",
        "thumbnail_url": ep.get("thumbnail_url"),
        "duration_seconds": ep.get("duration_seconds"),
    }
    try:
        if existing_id:
            updates = {}
            for field in ("title", "thumbnail_url", "duration_seconds"):
                nv = row.get(field)
                if nv:
                    updates[field] = nv
            if updates:
                db.table("episodes").update(updates).eq("id", existing_id).execute()
                STATS.episodes_updated += 1
                changed = list(updates.keys())
                con_upd(f"{ep_label} updated {changed} {'🖼' if 'thumbnail_url' in changed else ''}", indent=2)
            else:
                con_skip(f"{ep_label} already stored", indent=2)
            return existing_id
        else:
            res = db.table("episodes").insert(row).execute()
            ep_id = res.data[0]["id"]
            STATS.episodes_new += 1
            thumb_ok = "🖼" if row.get("thumbnail_url") else "❌"
            con_new(f"{ep_label} stored | thumb:{thumb_ok}", indent=2)
            return ep_id
    except Exception as e:
        log.warning(f"upsert_episode error {ep_label}: {e}")
        STATS.errors += 1
        con_err(f"{ep_label} error: {e}", indent=2)
        return None


def upsert_video_servers(db: Client, episode_id: str, servers: list[dict]) -> int:
    if not servers:
        return 0
    try:
        res = db.table("video_servers").select("stream_url").eq("episode_id", episode_id).execute()
        existing_urls = {r["stream_url"] for r in res.data}
    except Exception:
        existing_urls = set()
    added = 0
    for srv in servers:
        url = srv.get("stream_url", "")
        if not url or url in existing_urls:
            continue
        try:
            db.table("video_servers").insert({
                "episode_id": episode_id,
                "server_name": srv.get("server_name", "SERVER"),
                "stream_url": url,
                "quality": srv.get("quality", "1080p"),
                "language": srv.get("language", "Japanese"),
            }).execute()
            existing_urls.add(url)
            added += 1
            STATS.servers_new += 1
        except Exception as e:
            log.warning(f"video_server insert error: {e}")
    if added:
        con_ok(f"Stored {added} video server(s)", indent=3)
    return added


# ── Fix bad images ─────────────────────────────────────────────────────────────
def fix_bad_images(db: Client):
    try:
        res = db.table("content").select("id, title, thumbnail_url, poster_url, banner_url, type").execute()
        bad = [
            r for r in res.data
            if is_logo(r.get("thumbnail_url")) or is_logo(r.get("poster_url")) or not r.get("thumbnail_url")
        ]
    except Exception as e:
        con_err(f"fix_bad_images query error: {e}")
        return
    if not bad:
        con_ok("No bad image records found — all good!")
        return
    con_warn(f"Found {len(bad)} records with logo/missing images — fixing via direct URL guessing")
    total = len(bad)
    fixed = 0
    skipped = 0
    for i, row in enumerate(bad, 1):
        title  = row["title"]
        ct     = row["type"]
        row_id = row["id"]
        candidates = guess_content_urls(title, ct)
        alt_base = "movies" if ct == "series" else "series"
        for c in guess_content_urls(title, alt_base if ct == "series" else "series"):
            if c not in candidates:
                candidates.append(c)
        found_url: Optional[str] = None
        for candidate in candidates:
            html = fetch(candidate)
            if html:
                if slugify(title)[:8] in candidate or title[:6].lower() in html.lower():
                    found_url = candidate
                    break
        if not found_url:
            skipped += 1
            con_warn(f"[{i}/{total}] No page found for '{title}'", indent=1)
            continue
        data = parse_content_page(found_url, ct)
        if not data:
            skipped += 1
            continue
        updates: dict = {}
        for field in ("poster_url", "banner_url", "thumbnail_url"):
            new_val = data.get(field)
            if new_val and not is_logo(new_val):
                if is_logo(row.get(field)) or not row.get(field):
                    updates[field] = new_val
        if updates:
            try:
                db.table("content").update(updates).eq("id", row_id).execute()
                STATS.images_fixed += 1
                fixed += 1
                con_upd(f"[{i}/{total}] Fixed '{title}' → {list(updates.keys())}", indent=1)
            except Exception as e:
                skipped += 1
                con_err(f"[{i}/{total}] DB update error for '{title}': {e}", indent=1)
        else:
            skipped += 1
            con_skip(f"[{i}/{total}] No better images found for '{title}'", indent=1)
    con_ok(f"Image fix complete: {fixed} fixed · {skipped} unchanged/not-found out of {total}")


# ── Main pipeline ─────────────────────────────────────────────────────────────
def process_content(db: Client, url: str, content_type: str):
    data = parse_content_page(url, content_type)
    if not data:
        STATS.errors += 1
        con_warn(f"Could not scrape: {url}", indent=1)
        return

    episode_stubs = data.pop("_episodes", [])
    data["_episodes"] = episode_stubs
    episodes_to_process = episode_stubs

    content_id = upsert_content(db, data)
    if not content_id:
        return

    existing_eps = get_existing_episodes(db, content_id)
    title = data.get("title", url)

    new_eps = [e for e in episodes_to_process if (e["season_number"], e["episode_number"]) not in existing_eps]
    if episodes_to_process:
        con_sub(f"{title}: {len(episodes_to_process)} episodes ({len(existing_eps)} in DB, {len(new_eps)} new)")

    for ep_stub in episodes_to_process:
        key = (ep_stub["season_number"], ep_stub["episode_number"])
        existing_ep_id = existing_eps.get(key)
        ep_url = ep_stub.get("url", "")
        if ep_url:
            ep_data = parse_episode_page(ep_url)
            merged = {**ep_stub, **{k: v for k, v in ep_data.items() if v and k != "video_servers"}}
            video_servers = ep_data.get("video_servers", [])
        else:
            merged = ep_stub
            video_servers = []
        ep_id = upsert_episode(db, content_id, merged, existing_ep_id)
        if ep_id and video_servers:
            upsert_video_servers(db, ep_id, video_servers)

    if content_type == "movie" and not episode_stubs:
        key = (1, 1)
        existing_ep_id = existing_eps.get(key)
        ep_data = parse_episode_page(url)
        ep_id = upsert_episode(db, content_id, {
            "season_number": 1,
            "episode_number": 1,
            "title": data.get("title"),
            "thumbnail_url": data.get("thumbnail_url"),
            "duration_seconds": (data.get("duration_minutes") or 0) * 60 or None,
            **{k: v for k, v in ep_data.items() if v and k != "video_servers"},
        }, existing_ep_id)
        if ep_id:
            upsert_video_servers(db, ep_id, ep_data.get("video_servers", []))


def build_all_seeds(homepage_html: Optional[str]) -> list[str]:
    """
    Build the full list of listing URLs to crawl (as full URLs with optional query strings).
    For every category/genre/network/language seed we crawl:
      1. The base page (series by default)
      2. The same page with ?type=movies
    This ensures we don't miss movies-only content in any category.
    """
    # Start from hand-curated list + auto-discovered from homepage
    auto_seeds = autodiscover_taxonomy_seeds(homepage_html)
    all_paths = list(dict.fromkeys(BASE_CATEGORY_SEEDS + auto_seeds))

    full_urls: list[str] = []
    seen_urls: set[str] = set()

    for path in all_paths:
        base = urljoin(BASE_URL, path)
        # Normal (series/all)
        if base not in seen_urls:
            seen_urls.add(base)
            full_urls.append(base)
        # With ?type=movies — useful for network/language/category pages
        # Skip for /series/ and /movies/ themselves (already specific)
        if not path.rstrip("/").endswith(("/series", "/movies")):
            movies_url = base.rstrip("/") + "/" + "?type=movies"
            if movies_url not in seen_urls:
                seen_urls.add(movies_url)
                full_urls.append(movies_url)

    return full_urls


def run(progress_hook=None, new_title_hook=None):
    """
    progress_hook(current, total, title, url, status) — called after EVERY item.
    new_title_hook(title, content_type, episodes)      — called when a new title is inserted.
    """
    global STATS
    con_head("Senpai TV — Content Scraper")
    print(f"  {DIM}Press Ctrl+C at any time to stop safely — progress is always saved{RESET}\n", flush=True)

    if not SUPABASE_URL or not SUPABASE_KEY:
        con_err("SUPABASE_URL or SUPABASE_SERVICE_KEY not set!")
        sys.exit(1)

    db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    con_ok("Connected to Supabase")

    try:
        cnt_res = db.table("content").select("id", count="exact", head=True).execute()
        ep_res  = db.table("episodes").select("id", count="exact", head=True).execute()
        srv_res = db.table("video_servers").select("id", count="exact", head=True).execute()
        print(f"  {DIM}Database: {CYAN}{cnt_res.count or 0}{RESET}{DIM} titles · "
              f"{CYAN}{ep_res.count or 0}{RESET}{DIM} episodes · "
              f"{CYAN}{srv_res.count or 0}{RESET}{DIM} servers{RESET}\n", flush=True)
    except Exception:
        pass

    # Step 1: Fix bad images
    con_head("STEP 1 — Fixing existing bad image URLs")
    fix_bad_images(db)

    # Step 2: Discover all content
    con_head("STEP 2 — Discovering ALL content (sitemap + every category/network/language/genre page)")

    existing_set: set[str] = set()
    content_urls: list[tuple[str, str]] = []

    def add_url(u: str):
        clean = canonical_content_url(u)
        ct = classify_url(clean)
        if ct in ("series", "movie") and clean not in existing_set:
            existing_set.add(clean)
            content_urls.append((clean, ct))

    # 2a: Sitemap
    con_ok("Reading XML sitemap index…")
    all_sitemap_urls = get_sitemap_urls(SITEMAP_INDEX)
    before = len(content_urls)
    for u in all_sitemap_urls:
        add_url(u)
    con_ok(f"Sitemap: {len(all_sitemap_urls)} total URLs → {len(content_urls) - before} content pages added")

    # 2b: Homepage auto-discovery + all seeds
    con_ok("Scanning homepage for extra category/genre/network/language pages…")
    homepage_html = fetch(BASE_URL)
    all_seed_urls = build_all_seeds(homepage_html)
    con_ok(f"Will crawl {len(all_seed_urls)} listing pages (including ?type=movies variants)")

    for i, seed_url in enumerate(all_seed_urls, 1):
        try:
            extra = crawl_listing(seed_url)
            before = len(content_urls)
            for u in extra:
                add_url(u)
            added = len(content_urls) - before
            label = seed_url.replace(BASE_URL, "")
            if added:
                con_new(f"  [{i}/{len(all_seed_urls)}] {label} → +{added} new URLs ({len(content_urls)} total)")
            else:
                con_skip(f"  [{i}/{len(all_seed_urls)}] {label} → 0 new")
        except Exception as e:
            con_warn(f"  [{i}/{len(all_seed_urls)}] {seed_url} crawl error: {e}")

    series_count = sum(1 for _, ct in content_urls if ct == "series")
    movie_count  = sum(1 for _, ct in content_urls if ct == "movie")
    total_discovered = len(content_urls)
    con_ok(f"TOTAL to scrape: {total_discovered} unique titles ({series_count} series · {movie_count} movies)")

    if progress_hook:
        progress_hook(0, total_discovered, "Discovery complete", "", "discovered")

    # Step 3: Scrape all content
    con_head(f"STEP 3 — Scraping {total_discovered} content pages")

    for i, (url, ct) in enumerate(content_urls, 1):
        title_slug = urlparse(url).path.strip("/").split("/")[-1].replace("-", " ").title()
        try:
            con_progress(i, total_discovered, title_slug)
            before_new = STATS.content_new
            before_ep  = STATS.episodes_new
            process_content(db, url, ct)
            print(flush=True)

            if STATS.content_new > before_new:
                actual_title = title_slug
                ep_count = STATS.episodes_new - before_ep
                if new_title_hook:
                    new_title_hook(actual_title, ct, ep_count)
                status = "new"
            else:
                status = "updated/skipped"

            if progress_hook:
                progress_hook(i, total_discovered, title_slug, url, status)

        except KeyboardInterrupt:
            con_progress_done()
            print(f"\n{YELLOW}{BOLD}  ⚠  Interrupted — progress is saved!{RESET}\n")
            if progress_hook:
                progress_hook(i, total_discovered, title_slug, url, "interrupted")
            STATS.report()
            sys.exit(0)
        except Exception as e:
            log.error(f"Error processing {url}: {e}", exc_info=True)
            STATS.errors += 1
            con_err(f"Error: {url}: {e}")
            if progress_hook:
                progress_hook(i, total_discovered, title_slug, url, f"error: {e}")
            continue

    STATS.report()


def run_daemon(interval_hours: float = 6.0):
    cycle = 0
    while True:
        cycle += 1
        con_head(f"DAEMON CYCLE #{cycle}  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            run()
        except KeyboardInterrupt:
            print(f"\n{YELLOW}{BOLD}  ⚠  Daemon stopped by user.{RESET}\n")
            sys.exit(0)
        except Exception as e:
            con_err(f"Unhandled error in cycle #{cycle}: {e}")
            log.error("Unhandled error in daemon cycle", exc_info=True)
        next_run = datetime.fromtimestamp(time.time() + interval_hours * 3600)
        con_ok(f"Cycle #{cycle} done. Next run at {CYAN}{next_run.strftime('%H:%M:%S on %Y-%m-%d')}{RESET} ({interval_hours:.1f} h)")
        try:
            time.sleep(interval_hours * 3600)
        except KeyboardInterrupt:
            print(f"\n{YELLOW}{BOLD}  ⚠  Daemon stopped during sleep.{RESET}\n")
            sys.exit(0)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AnimeSalt scraper pipeline")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--interval", type=float, default=6.0, metavar="HOURS")
    args = parser.parse_args()
    if args.daemon:
        run_daemon(interval_hours=args.interval)
    else:
        run()
