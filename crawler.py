import re
import requests
import time
import json
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urljoin, urlparse

# config
ROOT = Path(__file__).parent
OUT_M3U = ROOT / "playlist.m3u"
OUT_JSON = ROOT / "channels.json"
USER_AGENT = "Mozilla/5.0 (compatible; IPTV-Crawler/1.0)"
TIMEOUT = 12
MAX_PER_SOURCE = 10       
MAX_TOTAL = 300           
SLEEP_BETWEEN = 0.4       

# источники: основной — iptv-org country file (raw)
IPTV_ORG_RU = "https://iptv-org.github.io/iptv/countries/ru.m3u"

# шаблоны для поиска ссылок .m3u8 и прямых HTTP-медиа ссылок
M3U8_RE = re.compile(r"https?://[^\s'\"<>]+\.m3u8(?:\?[^\s'\"<>]*)?", re.IGNORECASE)
M3U_RE = re.compile(r"https?://[^\s'\"<>]+\.m3u(?:\?[^\s'\"<>]*)?", re.IGNORECASE)

# вспомогательные функции
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def safe_get(url, allow_redirects=True):
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=allow_redirects)
        return r
    except Exception as e:
        # print("GET err", url, e)
        return None


def safe_head(url):
    try:
        r = session.head(url, timeout=TIMEOUT, allow_redirects=True)
        return r
    except Exception:
        return None


def find_m3u8_in_text(text):
    m = M3U8_RE.findall(text)
    return list(dict.fromkeys(m))  # dedupe keeping order


def is_m3u8_url_ok(url):
    """
    Try HEAD then small GET to determine if URL looks like a playable HLS manifest.
    """
    try:
        h = safe_head(url)
        if h and 200 <= h.status_code < 400:
            ctype = h.headers.get("content-type","").lower()
            if "application/vnd.apple.mpegurl" in ctype or "vnd.apple.mpegurl" in ctype or ".m3u8" in url.lower():
                return True
        # fallback to GET small
        g = safe_get(url)
        if g and g.status_code == 200:
            txt = g.text or ""
            if "#EXTM3U" in txt or "EXTINF" in txt:
                return True
    except Exception:
        pass
    return False


def parse_m3u_file(text):
    """
    If the source is an m3u file, extract contained m3u8 or HTTP streams.
    """
    found = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # if it's an URL to another playlist or m3u8 - capture
        if line.lower().startswith("http"):
            if ".m3u8" in line or ".m3u" in line:
                found.append(line)
    return found


def extract_from_url(url):
    """
    Fetch url, try to extract m3u8 links from the content and from any linked m3u files.
    Returns list of candidate m3u8 URLs.
    """
    candidates = []
    r = safe_get(url)
    if not r:
        return candidates
    text = r.text or ""
    # if it's an m3u playlist
    if ".m3u" in url.lower() or "#EXTM3U" in text[:200]:
        # parse m3u for nested links
        candidates.extend(parse_m3u_file(text))
        # also search text for m3u8
        candidates.extend(find_m3u8_in_text(text))
        return list(dict.fromkeys(candidates))
    # otherwise, try to find .m3u8 links in HTML
    candidates.extend(find_m3u8_in_text(text))
    # also search for <source>, <video> tags
    try:
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup.find_all(["source", "video", "iframe", "script", "link"]):
            src = tag.get("src") or tag.get("data-src") or tag.get("href")
            if src:
                # make absolute if necessary
                src = urljoin(url, src)
                if ".m3u8" in src:
                    candidates.append(src)
                elif ".m3u" in src:
                    candidates.append(src)
        # look for JS variables with m3u8
        scripts = " ".join([s.get_text(" ", strip=True) for s in soup.find_all("script") if s])
        candidates.extend(find_m3u8_in_text(scripts))
    except Exception:
        pass
    # dedupe and return
    unique = []
    for it in candidates:
        if it not in unique:
            unique.append(it)
    return unique


def crawl_iptv_org(country_m3u_url):
    """
    Download iptv-org country m3u and extract candidate sources and nested links.
    We'll treat each line that is not a comment as a possible source and also parse the file content.
    """
    print("Fetching iptv-org country file:", country_m3u_url)
    r = safe_get(country_m3u_url)
    if not r or r.status_code != 200:
        print("Не удалось скачать", country_m3u_url)
        return []
    text = r.text or ""
    # first attempt: find direct m3u8 links inside
    found = find_m3u8_in_text(text)
    # parse m3u lines that are urls pointing to other m3u files or pages
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("http"):
            lines.append(line)
    # combine: lines may include links to channel pages or other raw files
    candidates = list(dict.fromkeys(found + lines))
    print("Found", len(candidates), "initial candidates in iptv-org file")
    return candidates


def normalize_name_from_url(url):
    parsed = urlparse(url)
    name = parsed.netloc + parsed.path
    name = name.strip("/").replace("/", "_")
    if len(name) > 80:
        name = name[:80]
    return name


def main():
    candidates = []
    # 1) seed from iptv-org country file
    seeds = crawl_iptv_org(IPTV_ORG_RU)
    candidates.extend(seeds)

    # optionally: add other seed pages (common aggregators). Can expand later.
    # e.g. candidates.append("https://iptv-org.github.io/iptv/index.m3u")  # already included via country

    final_candidates = []
    seen_sources = set()
    # for each seed, try to extract m3u8 links
    for src in candidates:
        if len(final_candidates) >= MAX_TOTAL:
            break
        if src in seen_sources:
            continue
        seen_sources.add(src)
        time.sleep(SLEEP_BETWEEN)
        # if seed itself looks like m3u8 - add directly
        if ".m3u8" in src.lower():
            final_candidates.append(src)
            continue
        # else try extracting from the page or nested playlist
        extracted = extract_from_url(src)
        # if extraction yields many links, keep limited amount
        cnt = 0
        for ex in extracted:
            if ex not in final_candidates:
                final_candidates.append(ex)
                cnt += 1
            if cnt >= MAX_PER_SOURCE:
                break
        # also keep the seed if it looks like m3u (some seeds are direct m3u links)
        if ".m3u" in src.lower() and src not in final_candidates:
            final_candidates.append(src)

    print("Total raw candidate m3u/m3u8 links:", len(final_candidates))

    # Now test candidates, keep only those that respond as m3u8
    good = []
    bad = []
    for url in final_candidates:
        if len(good) >= MAX_TOTAL:
            break
        # small normalization
        url = url.strip()
        # skip non-http
        if not url.lower().startswith("http"):
            continue
        # try to follow redirects once
        time.sleep(SLEEP_BETWEEN)
        ok = is_m3u8_url_ok(url)
        if ok:
            print("OK:", url)
            good.append(url)
        else:
            # try to fetch page and search inside for m3u8 (rare)
            time.sleep(SLEEP_BETWEEN)
            r = safe_get(url)
            if r and r.status_code == 200:
                txt = r.text or ""
                inner = find_m3u8_in_text(txt)
                added_inner = False
                for inn in inner:
                    if inn not in good:
                        time.sleep(SLEEP_BETWEEN)
                        if is_m3u8_url_ok(inn):
                            print("OK(inner):", inn, "(from", url, ")")
                            good.append(inn)
                            added_inner = True
                            break
                if not added_inner:
                    bad.append(url)
            else:
                bad.append(url)

    print("Good m3u8 count:", len(good), "Bad candidates:", len(bad))

    # Build channels.json entries
    channels = []
    for idx, url in enumerate(good):
        name = normalize_name_from_url(url)
        ch = {
            "name": name,
            "tvg-id": name.lower().replace(" ", "_"),
            "group": "auto",
            "type": "direct",
            "source": url,
            "headers": {}
        }
        channels.append(ch)

    # write channels.json
    OUT_JSON.write_text(json.dumps(channels, ensure_ascii=False, indent=2), encoding='utf-8')
    print("Wrote", OUT_JSON)

    # write playlist.m3u
    lines = ["#EXTM3U"]
    for ch in channels:
        nm = ch["name"]
        url = ch["source"]
        lines.append(f'#EXTINF:-1,{nm}')
        lines.append(url)
    OUT_M3U.write_text("\n".join(lines) + "\n", encoding='utf-8')
    print("Wrote", OUT_M3U, "channels:", len(channels))


if __name__ == "__main__":
    main()
