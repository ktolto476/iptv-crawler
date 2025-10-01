import re
import requests
import time
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse

# --- Конфиг ---
ROOT = Path(__file__).parent
OUT_M3U = ROOT / "playlist.m3u"
OUT_JSON = ROOT / "channels.json"
SOURCES = ROOT / "sources.txt"

USER_AGENT = "Mozilla/5.0 (compatible; IPTV-Crawler/1.0)"
TIMEOUT = 10
MAX_TOTAL = 200
SLEEP = 0.6

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

M3U8_RE = re.compile(r"https?://[^\s'\"<>]+\.m3u8(?:\?[^\s'\"<>]*)?", re.I)

def safe_get(url):
    try:
        return session.get(url, timeout=TIMEOUT, allow_redirects=True)
    except Exception:
        return None

def is_m3u8_url_ok(url):
    r = safe_get(url)
    if not r or r.status_code != 200:
        return False
    txt = r.text[:500]  # читаем только начало
    if "#EXTM3U" in txt:
        return True
    ctype = r.headers.get("content-type", "").lower()
    if "mpegurl" in ctype or ".m3u8" in url.lower():
        return True
    return False

def parse_m3u(text):
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line.startswith("http"):
            if line not in urls:
                urls.append(line)
    return urls

def normalize_name(url):
    parsed = urlparse(url)
    name = parsed.netloc + parsed.path
    return name.strip("/").replace("/", "_")[:80]

def main():
    candidates = []

    # --- читаем источники ---
    if not SOURCES.exists():
        print("Нет файла sources.txt")
        return
    for src in SOURCES.read_text().splitlines():
        src = src.strip()
        if not src or src.startswith("#"):
            continue
        print(f"Источник: {src}")
        r = safe_get(src)
        if not r or r.status_code != 200:
            continue
        if "#EXTM3U" in r.text:
            urls = parse_m3u(r.text)
            candidates.extend(urls)
        else:
            # искать прямые m3u8
            candidates.extend(M3U8_RE.findall(r.text))

    print("Всего кандидатов:", len(candidates))

    # --- проверка ---
    good = []
    seen = set()
    for url in candidates:
        if len(good) >= MAX_TOTAL:
            break
        url = url.strip()
        if url in seen:
            continue
        seen.add(url)
        time.sleep(SLEEP)
        if is_m3u8_url_ok(url):
            print("OK:", url)
            good.append(url)

    print("Рабочих ссылок:", len(good))

    # --- запись channels.json ---
    channels = []
    for url in good:
        name = normalize_name(url)
        channels.append({
            "name": name,
            "tvg-id": name.lower(),
            "group": "auto",
            "type": "direct",
            "source": url,
            "headers": {}
        })
    OUT_JSON.write_text(json.dumps(channels, ensure_ascii=False, indent=2), "utf-8")

    # --- запись playlist.m3u ---
    lines = ["#EXTM3U"]
    for ch in channels:
        lines.append(f"#EXTINF:-1,{ch['name']}")
        lines.append(ch["source"])
    OUT_M3U.write_text("\n".join(lines), "utf-8")

    print("Сохранено:", OUT_JSON, OUT_M3U)

if __name__ == "__main__":
    main()
