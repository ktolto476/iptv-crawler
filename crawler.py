import re
import requests
import time
import json
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).parent
OUT_M3U = ROOT / "playlist.m3u"
OUT_JSON = ROOT / "channels.json"
SOURCES = ROOT / "sources.txt"

USER_AGENT = "Mozilla/5.0 (compatible; IPTV-Crawler/1.1)"
TIMEOUT = 12
MAX_TOTAL = 200       
MAX_CANDIDATES = 800  
SLEEP = 0.6


ALLOWED_COUNTRIES = ["ru"]
ALLOWED_TZ = ["UTC+5", "GMT+5"]  

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

M3U8_RE = re.compile(r"https?://[^\s'\"<>]+\.m3u8(?:\?[^\s'\"<>]*)?", re.I)


def safe_get(url):
    
    try:
        with session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True) as r:
            chunk = b""
            for part in r.iter_content(2048):
                chunk += part
                if len(chunk) > 200_000:
                    break
            text = chunk.decode(errors="ignore")
            r._text = text
            r._status = r.status_code
            return r
    except Exception as e:
        print("[ERR] safe_get:", url, e)
        return None


def is_m3u8_url_ok(url):
    
    r = safe_get(url)
    if not r or r._status != 200:
        return False
    txt = r._text[:500]
    if "#EXTM3U" in txt or "#EXT-X-TARGETDURATION" in txt:
        return True
    ctype = r.headers.get("content-type", "").lower()
    if "mpegurl" in ctype or ".m3u8" in url.lower():
        return True
    return False


def parse_m3u(text):
    
    urls = []
    current_name = None
    current_tvgid = None
    current_group = None
    current_tz = None

    channels = []

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            
            current_name = None
            current_tvgid = None
            current_group = None
            current_tz = None

            
            if 'tvg-country=' in line:
                m = re.search(r'tvg-country="([^"]+)"', line)
                if m:
                    current_tvgid = m.group(1).lower()

            
            if 'tvg-timezone=' in line:
                m = re.search(r'tvg-timezone="([^"]+)"', line)
                if m:
                    current_tz = m.group(1)

            
            if "," in line:
                current_name = line.split(",")[-1].strip()

        elif line and line.startswith("http"):
            url = line
            channels.append({
                "url": url,
                "name": current_name or "Unknown",
                "country": current_tvgid,
                "tz": current_tz
            })

    return channels


def normalize_name(url):
    parsed = urlparse(url)
    return (parsed.netloc + parsed.path).strip("/").replace("/", "_")[:80]


def main():
    candidates = []

    if not SOURCES.exists():
        print("Нет sources.txt")
        return

    for src in SOURCES.read_text().splitlines():
        src = src.strip()
        if not src or src.startswith("#"):
            continue
        print(f"[SRC] {src}")
        r = safe_get(src)
        if not r or r._status != 200:
            continue

        text = r._text
        if "#EXTM3U" in text:
            chans = parse_m3u(text)
            candidates.extend(chans)
        else:
            for url in M3U8_RE.findall(text):
                candidates.append({"url": url, "name": None, "country": None, "tz": None})

        if len(candidates) >= MAX_CANDIDATES:
            break

    print("[INFO] Найдено кандидатов:", len(candidates))

    
    filtered = []
    for c in candidates:
        if c["country"] and c["country"] not in ALLOWED_COUNTRIES:
            continue
        if c["tz"] and not any(tz in c["tz"] for tz in ALLOWED_TZ):
            continue
        filtered.append(c)

    print("[INFO] После фильтров:", len(filtered))

    
    good = []
    seen = set()
    for c in filtered:
        url = c["url"]
        if not url or url in seen:
            continue
        seen.add(url)
        if len(good) >= MAX_TOTAL:
            break
        print(f"[CHECK] {url}")
        time.sleep(SLEEP)
        if is_m3u8_url_ok(url):
            print("[OK]", url)
            good.append(c)

    print("[RESULT] Рабочих ссылок:", len(good))

    
    channels = []
    for c in good:
        nm = c["name"] or normalize_name(c["url"])
        channels.append({
            "name": nm,
            "tvg-id": nm.lower(),
            "group": "auto",
            "type": "direct",
            "source": c["url"],
            "headers": {}
        })
    OUT_JSON.write_text(json.dumps(channels, ensure_ascii=False, indent=2), "utf-8")

    
    lines = ["#EXTM3U"]
    for ch in channels:
        lines.append(f"#EXTINF:-1,{ch['name']}")
        lines.append(ch["source"])
    OUT_M3U.write_text("\n".join(lines), "utf-8")

    print("[DONE] Сохранено:", OUT_JSON, OUT_M3U)


if __name__ == "__main__":
    main()
