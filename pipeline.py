#!/usr/bin/env python3
"""
AnimeSalt.ac → Supabase Scraper Pipeline
Run:  python3 scraper/pipeline.py
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
from urllib.parse import urljoin, urlparse
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
    """Print a colored status line to console."""
    pad = "  " * indent
    print(f"{DIM}{ts()}{RESET} {pad}{color}{BOLD}{symbol}{RESET} {msg}")

def con_ok(msg: str, indent: int = 0):      con(GREEN,   "✓", msg, indent)
def con_new(msg: str, indent: int = 0):     con(CYAN,    "+", msg, indent)
def con_upd(msg: str, indent: int = 0):     con(BLUE,    "↑", msg, indent)
def con_skip(msg: str, indent: int = 0):    con(DIM,     "–", msg, indent)
def con_warn(msg: str, indent: int = 0):    con(YELLOW,  "⚠", msg, indent)
def con_err(msg: str, indent: int = 0):     con(RED,     "✗", msg, indent)
def con_head(msg: str):                     print(f"\n{MAGENTA}{BOLD}{'━'*60}{RESET}\n{MAGENTA}{BOLD}  {msg}{RESET}\n{MAGENTA}{BOLD}{'━'*60}{RESET}")
def con_sub(msg: str):                      print(f"\n{BLUE}{BOLD}  ▸ {msg}{RESET}")
def con_progress(current: int, total: int, title: str):
    pct = int(current / total * 40) if total else 0
    bar = f"[{GREEN}{'█'*pct}{DIM}{'░'*(40-pct)}{RESET}]"
    print(f"\r{DIM}{ts()}{RESET} {bar} {CYAN}{current}/{total}{RESET} {title[:50]}", end="", flush=True)
def con_progress_done():
    print()  # newline after progress bar

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
# Patterns in URLs that indicate non-content images (channel logos, icons, etc.)
# These are matched case-insensitively in the URL path only (not domain)
EXCLUDE_PATH_PATTERNS = [
    "AnimeSaltLong", "cropped-AnimeSalt",
    "sonyay", "sony-yay",
    "nickelodeon", "disney", "cartoon-network", "pogo-",
]
# Exclude if URL filename contains these (separate from domain)
EXCLUDE_FILENAME_PATTERNS = [
    "favicon", "watermark",
]
REQUEST_DELAY  = (1.5, 3.0)   # seconds between requests (min, max)
MAX_RETRIES    = 3

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# ── Logging (file next to this script — works on any server) ──────────────────
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
    """Fetch a URL with retries and polite delay."""
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(*REQUEST_DELAY))
            r = session.get(url, timeout=20)
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
    # Check path portion only (not the domain) so img.animesalt.ac CDN is allowed
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    for pattern in EXCLUDE_PATH_PATTERNS:
        if pattern.lower() in path_lower:
            return True
    # Filename-level checks
    filename = path_lower.split("/")[-1]
    for pattern in EXCLUDE_FILENAME_PATTERNS:
        if pattern.lower() in filename:
            return True
    return False


def best_img(*candidates) -> Optional[str]:
    """Return first non-logo, non-empty image URL."""
    for c in candidates:
        if c and not is_logo(c) and c.startswith("http"):
            return c.strip()
    return None


def normalize_url(url: Optional[str]) -> Optional[str]:
    """Fix protocol-relative URLs like //image.tmdb.org/... → https://image.tmdb.org/..."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def extract_image(tag) -> Optional[str]:
    """Pull best src from an img tag (lazy-load aware)."""
    if tag is None:
        return None
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        val = tag.get(attr)
        if val and not val.startswith("data:") and not is_logo(val):
            return normalize_url(val)
    return None


# ── Sitemap discovery ─────────────────────────────────────────────────────────
def get_sitemap_urls(sitemap_url: str) -> list[str]:
    """Recursively expand WordPress sitemap index and return all page URLs."""
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

    # sitemap index → recurse
    sub_maps = root.findall("sm:sitemap/sm:loc", ns)
    if sub_maps:
        urls = []
        for sm in sub_maps:
            urls.extend(get_sitemap_urls(sm.text.strip()))
        return urls

    # url set
    return [u.text.strip() for u in root.findall("sm:url/sm:loc", ns)]


def classify_url(url: str) -> Optional[str]:
    """Return 'series', 'movie', 'episode', or None."""
    p = urlparse(url).path
    if p.startswith("/series/"):
        return "series"
    if p.startswith("/movies/"):
        return "movie"
    if p.startswith("/episode/"):
        return "episode"
    return None


# ── Category / pagination fallback ───────────────────────────────────────────
CATEGORY_SEEDS = [
    "/series/",
    "/movies/",
    "/category/anime/",
    "/category/cartoon/",
    "/category/anime-movie/",
    "/category/dubbed/",
    "/category/subbed/",
    "/category/ongoing/",
    "/category/completed/",
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
]


def slugify(title: str) -> str:
    """Convert a title to an animesalt-style URL slug."""
    s = title.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def guess_content_urls(title: str, content_type: str) -> list[str]:
    """Return a list of candidate animesalt.ac URLs for a title."""
    base = "series" if content_type == "series" else "movies"
    variants = [title]
    # remove trailing qualifiers like "Season 2", "(2023)" etc.
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
        # try with colon replaced by empty
        slug2 = slugify(v.replace(":", "").replace("'", ""))
        if slug2 and slug2 not in seen_slugs:
            seen_slugs.add(slug2)
            urls.append(f"{BASE_URL}/{base}/{slug2}/")
    return urls


def discover_extra_seeds(homepage_html: Optional[str]) -> list[str]:
    """Parse the homepage nav to find all category/genre listing pages."""
    seeds: list[str] = []
    if not homepage_html:
        return seeds
    s = soup(homepage_html)
    for a in s.select("a[href]"):
        href = str(a.get("href", ""))
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc.rstrip("www.") not in ("animesalt.ac", ""):
            continue
        path = parsed.path
        if re.match(r"^/(category|genre|tag)/[^/]+/?$", path):
            seeds.append(path if path.endswith("/") else path + "/")
    return list(dict.fromkeys(seeds))


def crawl_listing(path: str) -> list[str]:
    """Paginate through a listing page and collect content URLs."""
    found = []
    page = 1
    while True:
        url = urljoin(BASE_URL, path if page == 1 else f"{path.rstrip('/')}/page/{page}/")
        html = fetch(url)
        if not html:
            break
        s = soup(html)
        links = s.select("a[href]")
        page_found = []
        for a in links:
            href = a["href"]
            if classify_url(href) in ("series", "movie"):
                page_found.append(href)
        if not page_found:
            break
        found.extend(page_found)
        log.info(f"  Listing {url}: found {len(page_found)} items (total {len(found)})")
        # check if next page exists
        nxt = s.select_one("a.next, a[rel=next], .nav-previous a")
        if not nxt:
            break
        page += 1
        if page > 500:
            break
    return list(dict.fromkeys(found))  # deduplicate, preserve order


# ── Content page scraper ──────────────────────────────────────────────────────
def parse_content_page(url: str, content_type: str) -> Optional[dict]:
    """
    Scrape a series or movie page.
    Returns dict with keys matching the `content` table + extras.
    """
    html = fetch(url)
    if not html:
        return None
    s = soup(html)

    # ── Title
    title = (
        (s.find("h1") or s.find("h2"))
        and (s.find("h1") or s.find("h2")).get_text(strip=True)
    )
    if not title:
        title_tag = s.find("meta", property="og:title")
        title = title_tag["content"].strip() if title_tag else url.split("/")[-2]

    # Clean title (remove site name suffixes)
    title = re.sub(r"\s*[-|]\s*Anime Salt.*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s*[-|]\s*Watch Now.*$", "", title, flags=re.IGNORECASE).strip()

    # ── Description
    desc_tag = s.find("meta", attrs={"name": "description"})
    og_desc  = s.find("meta", property="og:description")
    overview_el = (
        s.select_one(".overview, .description, .sinopse, [class*='overview'], [class*='sinopse']")
    )
    description = None
    if overview_el:
        description = overview_el.get_text(separator=" ", strip=True)
    elif og_desc and og_desc.get("content"):
        description = og_desc["content"].strip()
    elif desc_tag and desc_tag.get("content"):
        description = desc_tag["content"].strip()

    # ── Images ────────────────────────────────────────────────────────────────
    # Priority: og:image (WordPress featured image) > TMDB > CDN > any real img

    # og:image — most reliable on WordPress; always the featured/cover image
    og_img = s.find("meta", property="og:image")
    og_img_url = normalize_url(og_img["content"].strip()) if og_img and og_img.get("content") else None
    og_valid = og_img_url if not is_logo(og_img_url) else None

    # Scan every <img> on the page; bucket by origin
    all_imgs = s.select("img[src], img[data-src], img[data-lazy-src], img[data-original]")
    tmdb_images: list[str] = []
    cdn_images:  list[str] = []   # img.animesalt.ac or CDN
    wp_images:   list[str] = []   # animesalt.ac wp-content (not logos)
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

    # Also try background-image CSS on hero/banner divs
    bg_images: list[str] = []
    for el in s.select("[style*='background']"):
        style = el.get("style", "")
        m = re.search(r"url\(['\"]?(https?://[^'\")\s]+)['\"]?\)", style)
        if m:
            u = m.group(1)
            if not is_logo(u):
                bg_images.append(u)

    # Dedicated element selectors
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

    # ── thumbnail_url: the main cover shown in listings
    #   Best: og:image → first CDN/WP image → TMDB → any real
    thumbnail_url = best_img(
        og_valid,
        *cdn_images,
        *wp_images,
        poster_from_el,
        *tmdb_images,
        *bg_images,
        all_real[0] if all_real else None,
    )

    # ── poster_url: high-quality art (TMDB preferred)
    #   Best: element selector → TMDB → og:image → CDN → any
    poster_url = best_img(
        poster_from_el,
        *tmdb_images,
        og_valid,
        *cdn_images,
        *wp_images,
        all_real[0] if all_real else None,
    )

    # ── banner_url: wide landscape/backdrop
    #   Best: element selector → CDN/WP → bg-image → og:image → TMDB
    banner_url = best_img(
        banner_from_el,
        *bg_images,
        *cdn_images,
        *wp_images,
        og_valid,
        *tmdb_images,
        all_real[0] if all_real else None,
    )

    # Ensure thumbnail is never None if we have any image at all
    any_img = best_img(thumbnail_url, poster_url, banner_url, og_valid, *all_real)
    if not thumbnail_url:
        thumbnail_url = any_img
    if not poster_url:
        poster_url = any_img
    if not banner_url:
        banner_url = any_img

    log.debug(
        f"  Images for '{url}': "
        f"thumb={thumbnail_url!r} poster={poster_url!r} banner={banner_url!r} "
        f"(tmdb={len(tmdb_images)} cdn={len(cdn_images)} wp={len(wp_images)} real={len(all_real)})"
    )

    # ── Year
    year = None
    year_el = s.select_one(".year, [class*='year'], .date, time")
    if year_el:
        m = re.search(r"\b(19|20)\d{2}\b", year_el.get_text())
        if m:
            year = int(m.group())
    if not year:
        # Try meta
        pub = s.find("meta", property="article:published_time")
        if pub and pub.get("content"):
            m = re.search(r"(20\d{2})", pub["content"])
            if m:
                year = int(m.group())

    # ── Duration (movies)
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

    # ── Genres
    genres = []
    # Look for genre links in the page
    genre_links = s.select(
        "a[href*='/category/genre/'], a[href*='/genre/'], "
        ".genres a, .categorias a, [class*='genre'] a"
    )
    for a in genre_links:
        g = a.get_text(strip=True)
        if g and len(g) < 40:
            genres.append(g)
    genres = list(dict.fromkeys(genres))

    # ── Languages
    languages = []
    lang_links = s.select(
        "a[href*='/category/language/'], "
        ".languages a, [class*='language'] a"
    )
    for a in lang_links:
        l = a.get_text(strip=True)
        if l and len(l) < 30:
            languages.append(l)
    languages = list(dict.fromkeys(languages))
    primary_language = languages[0] if languages else "Japanese"

    # ── Seasons / Episodes list (for series)
    episodes = []
    if content_type == "series":
        episodes = parse_episode_list(s, url)

    # ── Status
    status = "completed" if content_type == "movie" else "ongoing"
    status_el = s.select_one(".status, [class*='status']")
    if status_el:
        st = status_el.get_text(strip=True).lower()
        if "complet" in st or "finished" in st:
            status = "completed"
        elif "ongoing" in st or "airing" in st:
            status = "ongoing"

    # ── Rating
    rating = 0.0
    rating_el = s.select_one(".rating, .score, [class*='rating'], [class*='score']")
    if rating_el:
        m = re.search(r"(\d+\.?\d*)", rating_el.get_text())
        if m:
            rating = float(m.group(1))

    # ── Source URL slug (for dedup)
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
        "_episodes": episodes,
        "_source_url": url,
        "_slug": slug,
    }


def parse_episode_list(s: BeautifulSoup, series_url: str) -> list[dict]:
    """Extract episode links and metadata from a series page."""
    episodes = []
    seen_hrefs = set()

    # Primary method: article.episodes containers (torofilm theme)
    # Each article has: .post-thumbnail img (episode thumb) + a.lnk-blk (episode URL)
    for article in s.select("article.episodes, article.post.episodes, .episodes-container article"):
        # Find the episode link
        a = article.select_one("a.lnk-blk, a[href*='/episode/']")
        if not a:
            continue
        href = a.get("href", "")
        if not href or "/episode/" not in href or href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        ep_slug = urlparse(href).path.strip("/").split("/")[-1]
        season_num, ep_num = parse_episode_numbers(ep_slug)

        # Thumbnail: all img attrs (lazy-load aware), prefer CDN/WP over TMDB
        thumb = None
        for img_el in article.select("img"):
            v = extract_image(img_el)
            if v and not is_logo(v):
                thumb = v
                break  # first good image in the article card wins

        # Title: from num-epi or title span
        title_el = article.select_one(".num-epi, .episode-title, .title, h3, h4")
        ep_title = title_el.get_text(strip=True) if title_el else None
        if ep_title and len(ep_title) > 100:
            ep_title = None

        episodes.append({
            "url": href,
            "slug": ep_slug,
            "season_number": season_num,
            "episode_number": ep_num,
            "title": ep_title,
            "thumbnail_url": thumb,
            "description": None,
        })

    # Fallback method: if no article containers found, collect unique /episode/ hrefs
    if not episodes:
        for a in s.select("a[href*='/episode/']"):
            href = a.get("href", "")
            if not href or href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            ep_slug = urlparse(href).path.strip("/").split("/")[-1]
            season_num, ep_num = parse_episode_numbers(ep_slug)
            img_el = a.find("img")
            thumb = extract_image(img_el)
            episodes.append({
                "url": href,
                "slug": ep_slug,
                "season_number": season_num,
                "episode_number": ep_num,
                "title": None,
                "thumbnail_url": thumb,
                "description": None,
            })

    return episodes


def parse_episode_numbers(slug: str):
    """Parse '1x5' or 's1e5' or 'episode-5' from slug."""
    # e.g. cells-at-work-1x1
    m = re.search(r"(\d+)x(\d+)", slug)
    if m:
        return int(m.group(1)), int(m.group(2))
    # e.g. s1e5
    m = re.search(r"s(\d+)e(\d+)", slug, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    # e.g. episode-5
    m = re.search(r"episode[- _](\d+)", slug, re.IGNORECASE)
    if m:
        return 1, int(m.group(1))
    # last number
    m = re.findall(r"\d+", slug)
    if m:
        return 1, int(m[-1])
    return 1, 1


# ── Episode page scraper ──────────────────────────────────────────────────────
def parse_episode_page(url: str) -> dict:
    """
    Scrape a single episode page.
    Returns dict with thumbnail, title, description, and video_servers list.
    """
    html = fetch(url)
    if not html:
        return {}
    s = soup(html)

    # Title
    title = None
    h1 = s.find("h1")
    if h1:
        title = h1.get_text(strip=True)
        title = re.sub(r"\s*[-|]\s*Anime Salt.*$", "", title, flags=re.IGNORECASE).strip()

    # Description
    desc = None
    desc_el = s.select_one(".overview, .description, .sinopse")
    if desc_el:
        desc = desc_el.get_text(separator=" ", strip=True)

    # ── Thumbnail ─────────────────────────────────────────────────────────────
    # og:image on an episode page = the episode frame/screenshot thumbnail
    og_img = s.find("meta", property="og:image")
    og_img_url = normalize_url(og_img["content"].strip()) if og_img and og_img.get("content") else None
    og_valid = og_img_url if not is_logo(og_img_url) else None

    # Scan all imgs, bucket by source
    ep_tmdb:   list[str] = []
    ep_cdn:    list[str] = []
    ep_wp:     list[str] = []
    ep_all:    list[str] = []
    for img in s.select("img[src], img[data-src], img[data-lazy-src], img[data-original]"):
        v = extract_image(img)
        if not v or is_logo(v):
            continue
        ep_all.append(v)
        if "tmdb.org" in v or "image.tmdb" in v:
            ep_tmdb.append(v)
        elif "img.animesalt.ac" in v:
            ep_cdn.append(v)
        elif "animesalt.ac" in v and "wp-content" in v:
            ep_wp.append(v)

    # Specific selectors
    thumb_el = s.select_one(
        ".thumb img, .episode-img img, .post-thumbnail img, "
        "article img, .entry-content img, figure img, .featured-image img"
    )
    thumb_from_el = extract_image(thumb_el)

    # Best episode thumbnail: og:image > element > CDN > WP > TMDB > any
    thumb = best_img(
        og_valid,
        thumb_from_el,
        *ep_cdn,
        *ep_wp,
        *ep_tmdb,
        ep_all[0] if ep_all else None,
    )

    # Duration
    duration_seconds = None
    dur_el = s.select_one(".runtime, .duration, [class*='runtime'], [class*='duration']")
    if dur_el:
        m = re.search(r"(\d+)\s*min", dur_el.get_text(), re.IGNORECASE)
        if m:
            duration_seconds = int(m.group(1)) * 60

    # Video servers — look for iframes and server buttons
    video_servers = []
    seen_urls = set()

    # Method 1: iframes
    for iframe in s.select("iframe[src], iframe[data-src]"):
        src = iframe.get("src") or iframe.get("data-src") or ""
        if src and src not in seen_urls and src.startswith("http"):
            seen_urls.add(src)
            video_servers.append({
                "server_name": "Embed",
                "stream_url": src,
                "quality": "1080p",
                "language": "Japanese",
            })

    # Method 2: script tags with source/file variables
    for script in s.find_all("script"):
        text = script.string or ""
        # Look for file: "url" or source: [{file: "url"}]
        for m in re.finditer(r'(?:file|src|source)["\s:]+["\']?(https?://[^\s"\'<>]+)', text):
            src = m.group(1)
            if src not in seen_urls and any(ext in src for ext in [".m3u8", ".mp4", ".ts"]):
                seen_urls.add(src)
                video_servers.append({
                    "server_name": "Direct",
                    "stream_url": src,
                    "quality": "1080p",
                    "language": "Japanese",
                })

    # Method 3: data-* attributes on server buttons
    for btn in s.select("[data-src], [data-embed], [data-url], [data-video]"):
        for attr in ("data-src", "data-embed", "data-url", "data-video"):
            src = btn.get(attr, "")
            if src and src.startswith("http") and src not in seen_urls:
                seen_urls.add(src)
                server_name = btn.get_text(strip=True) or btn.get("data-server", "SERVER")
                # Try to get quality and language from nearby elements or parent
                quality = "1080p"
                lang = "Japanese"
                parent_text = (btn.parent or btn).get_text(strip=True).lower()
                for q in ["480p", "720p", "1080p", "4k"]:
                    if q in parent_text:
                        quality = q
                        break
                video_servers.append({
                    "server_name": server_name[:50],
                    "stream_url": src,
                    "quality": quality,
                    "language": lang,
                })

    # Method 4: Look for server tab links (common in torofilm theme)
    # server tabs have class like "server-item" or "player-option"
    for server_div in s.select(
        ".server-item, .player-option, .tab-server, .server, [class*='server-']"
    ):
        link = server_div.get("data-src") or server_div.get("data-embed") or ""
        if not link:
            a = server_div.find("a")
            link = a["href"] if a and a.get("href", "").startswith("http") else ""
        if link and link not in seen_urls and link.startswith("http"):
            seen_urls.add(link)
            name = server_div.get_text(strip=True) or "SERVER"
            video_servers.append({
                "server_name": name[:50],
                "stream_url": link,
                "quality": "1080p",
                "language": "Japanese",
            })

    return {
        "title": title,
        "description": desc,
        "thumbnail_url": thumb,
        "duration_seconds": duration_seconds,
        "video_servers": video_servers,
    }


# ── Supabase helpers ──────────────────────────────────────────────────────────
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
        res = db.table("content").select("id, title, poster_url, thumbnail_url, banner_url").eq("title", title).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        log.warning(f"find_content error: {e}")
        return None


def upsert_content(db: Client, data: dict) -> Optional[str]:
    """Insert or update content record. Returns content_id."""
    genres    = data.pop("_genres", [])
    languages = data.pop("_languages", [])
    episodes  = data.pop("_episodes", [])
    _source   = data.pop("_source_url", "")
    _slug     = data.pop("_slug", "")

    title = data.get("title", "?")

    # Check existing by title
    existing = find_content_by_title(db, title)

    try:
        if existing:
            content_id = existing["id"]
            updates = {}
            # Always write image fields if we have a good new value
            for field in ("poster_url", "banner_url", "thumbnail_url"):
                existing_val = existing.get(field)
                new_val      = data.get(field)
                if not new_val or is_logo(new_val):
                    continue  # skip if new value is bad
                if not existing_val or is_logo(existing_val) or existing_val != new_val:
                    updates[field] = new_val
            for field in ("description", "release_year", "duration_minutes", "language", "status"):
                if data.get(field) and not existing.get(field):
                    updates[field] = data[field]
            if updates:
                db.table("content").update(updates).eq("id", content_id).execute()
                STATS.content_updated += 1
                img_fields = [f for f in updates if "url" in f]
                if img_fields:
                    STATS.images_fixed += 1
                    con_upd(f"[UPDATED] {title} — fixed: {', '.join(updates.keys())}", indent=1)
                else:
                    con_upd(f"[UPDATED] {title} — {', '.join(updates.keys())}", indent=1)
            else:
                STATS.content_skipped += 1
                con_skip(f"[SKIP]    {title} — already complete", indent=1)
        else:
            res = db.table("content").insert(data).execute()
            content_id = res.data[0]["id"]
            STATS.content_new += 1
            thumb_ok = "🖼" if data.get("thumbnail_url") and not is_logo(data.get("thumbnail_url","")) else "❌"
            poster_ok= "🖼" if data.get("poster_url") and not is_logo(data.get("poster_url","")) else "❌"
            con_new(f"[NEW]     {title} | thumb:{thumb_ok} poster:{poster_ok} | {data.get('type','?')} {data.get('release_year','')}".strip(), indent=1)
    except Exception as e:
        log.error(f"upsert_content error '{title}': {e}")
        STATS.errors += 1
        con_err(f"[ERROR]   {title}: {e}", indent=1)
        return None

    # Genres
    for g_name in genres:
        g_id = get_or_create_genre(db, g_name)
        if g_id:
            try:
                db.table("content_genres").upsert(
                    {"content_id": content_id, "genre_id": g_id},
                    on_conflict="content_id,genre_id"
                ).execute()
            except Exception:
                pass

    if genres:
        log.debug(f"  Genres for '{title}': {genres}")

    return content_id


def get_existing_episodes(db: Client, content_id: str) -> dict:
    """Returns {(season_number, episode_number): episode_id}"""
    try:
        res = db.table("episodes").select("id, season_number, episode_number").eq("content_id", content_id).execute()
        return {(r["season_number"], r["episode_number"]): r["id"] for r in res.data}
    except Exception as e:
        log.warning(f"get_existing_episodes error: {e}")
        return {}


def upsert_episode(db: Client, content_id: str, ep_data: dict, existing_ep_id: Optional[str] = None) -> Optional[str]:
    """Upsert one episode row. Returns episode_id."""
    row = {
        "content_id": content_id,
        "season_number": ep_data.get("season_number", 1),
        "episode_number": ep_data.get("episode_number", 1),
        "title": ep_data.get("title"),
        "description": ep_data.get("description"),
        "thumbnail_url": ep_data.get("thumbnail_url"),
        "duration_seconds": ep_data.get("duration_seconds"),
    }
    ep_label = f"S{row['season_number']}E{row['episode_number']}"
    try:
        if existing_ep_id:
            updates = {}
            for k, v in row.items():
                if k in ("content_id", "season_number", "episode_number"):
                    continue
                if k == "thumbnail_url":
                    # Always write thumbnail if new value is good
                    if v and not is_logo(v):
                        updates[k] = v
                elif v:
                    updates[k] = v
            if updates:
                db.table("episodes").update(updates).eq("id", existing_ep_id).execute()
                STATS.episodes_updated += 1
                thumb_ok = "🖼" if updates.get("thumbnail_url") else ""
                con_upd(f"{ep_label} updated {list(updates.keys())} {thumb_ok}", indent=2)
            else:
                con_skip(f"{ep_label} already stored", indent=2)
            return existing_ep_id
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
    """Insert video server rows (skip duplicates by stream_url). Returns count added."""
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


# ── Fix existing bad records ──────────────────────────────────────────────────
def fix_bad_images(db: Client):
    """
    For every content row with a logo/missing image, directly construct
    the likely animesalt.ac URL from the title+type, fetch the page,
    and update the image fields. Much faster than crawling listing pages.
    """
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

        # Build candidate URLs from title slug
        candidates = guess_content_urls(title, ct)
        # Also try swapping series↔movies in case content was mis-typed
        alt_base = "movies" if ct == "series" else "series"
        for c in guess_content_urls(title, alt_base if ct == "series" else "series"):
            if c not in candidates:
                candidates.append(c)

        found_url: Optional[str] = None
        for candidate in candidates:
            html = fetch(candidate)
            if html:
                # Verify it's the right page (title should appear)
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

    # For movies: treat the movie itself as episode 1 if no episodes found
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


def run():
    con_head("AnimeSalt.ac → Supabase Scraper")
    print(f"  {DIM}Log file: scraper/scraper.log{RESET}")
    print(f"  {DIM}Press Ctrl+C at any time to stop safely — progress is always saved{RESET}\n")

    if not SUPABASE_URL or not SUPABASE_KEY:
        con_err("SUPABASE_URL or SUPABASE_SERVICE_KEY not set!")
        sys.exit(1)

    db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    con_ok("Connected to Supabase")

    # Show current DB state
    try:
        cnt_res = db.table("content").select("id", count="exact", head=True).execute()
        ep_res  = db.table("episodes").select("id", count="exact", head=True).execute()
        srv_res = db.table("video_servers").select("id", count="exact", head=True).execute()
        print(f"  {DIM}Database currently has: {CYAN}{cnt_res.count or 0}{RESET}{DIM} titles · "
              f"{CYAN}{ep_res.count or 0}{RESET}{DIM} episodes · "
              f"{CYAN}{srv_res.count or 0}{RESET}{DIM} servers{RESET}\n")
    except Exception:
        pass

    # Step 1: Fix existing bad images
    con_head("STEP 1 — Fixing existing bad image URLs")
    fix_bad_images(db)

    # Step 2: Discover all content via sitemap + comprehensive category crawl
    con_head("STEP 2 — Discovering ALL content (sitemap + every category/genre page)")

    existing_set: set[str] = set()
    content_urls: list[tuple[str, str]] = []

    def add_url(u: str):
        ct = classify_url(u)
        if ct in ("series", "movie") and u not in existing_set:
            existing_set.add(u)
            content_urls.append((u, ct))

    # 2a: XML sitemap (most complete source)
    con_ok("Reading XML sitemap index…")
    all_sitemap_urls = get_sitemap_urls(SITEMAP_INDEX)
    before = len(content_urls)
    for u in all_sitemap_urls:
        add_url(u)
    con_ok(f"Sitemap: {len(all_sitemap_urls)} total URLs → {len(content_urls) - before} content pages added")

    # 2b: Discover extra seeds from homepage nav
    con_ok("Scanning homepage for extra category/genre pages…")
    homepage_html = fetch(BASE_URL)
    homepage_seeds = discover_extra_seeds(homepage_html)
    all_seeds = list(dict.fromkeys(CATEGORY_SEEDS + homepage_seeds))
    con_ok(f"Will crawl {len(all_seeds)} listing pages (built-in + discovered from homepage)")

    # 2c: Crawl every seed with full pagination
    for i, seed in enumerate(all_seeds, 1):
        try:
            extra = crawl_listing(seed)
            before = len(content_urls)
            for u in extra:
                add_url(u)
            added = len(content_urls) - before
            if added:
                con_new(f"  [{i}/{len(all_seeds)}] {seed} → +{added} new URLs ({len(content_urls)} total)")
            else:
                con_skip(f"  [{i}/{len(all_seeds)}] {seed} → 0 new", indent=0)
        except Exception as e:
            con_warn(f"  [{i}/{len(all_seeds)}] {seed} crawl error: {e}")

    series_count = sum(1 for _, ct in content_urls if ct == "series")
    movie_count  = sum(1 for _, ct in content_urls if ct == "movie")
    con_ok(f"TOTAL to scrape: {len(content_urls)} unique titles ({series_count} series · {movie_count} movies)")

    # Step 3: Scrape all content
    con_head(f"STEP 3 — Scraping {len(content_urls)} content pages")
    total = len(content_urls)

    for i, (url, ct) in enumerate(content_urls, 1):
        try:
            slug = urlparse(url).path.strip("/").split("/")[-1]
            con_progress(i, total, slug)
            process_content(db, url, ct)
            # Print a newline after progress bar so content output appears below
            print()
        except KeyboardInterrupt:
            con_progress_done()
            print(f"\n{YELLOW}{BOLD}  ⚠  Interrupted by user — progress is saved!{RESET}")
            print(f"  {DIM}Run again to continue from where you left off.{RESET}\n")
            STATS.report()
            sys.exit(0)
        except Exception as e:
            log.error(f"Error processing {url}: {e}", exc_info=True)
            STATS.errors += 1
            con_err(f"Error: {url}: {e}")
            continue

    STATS.report()


def run_daemon(interval_hours: float = 6.0):
    """Run the scraper in an endless loop, pausing `interval_hours` between passes."""
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
        con_ok(
            f"Cycle #{cycle} done. Next run at "
            f"{CYAN}{next_run.strftime('%H:%M:%S on %Y-%m-%d')}{RESET}  "
            f"({interval_hours:.1f} h)"
        )
        try:
            time.sleep(interval_hours * 3600)
        except KeyboardInterrupt:
            print(f"\n{YELLOW}{BOLD}  ⚠  Daemon stopped during sleep.{RESET}\n")
            sys.exit(0)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AnimeSalt scraper pipeline")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously, repeating every --interval hours",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=6.0,
        metavar="HOURS",
        help="Hours to wait between daemon cycles (default: 6)",
    )
    args = parser.parse_args()

    if args.daemon:
        run_daemon(interval_hours=args.interval)
    else:
        run()
