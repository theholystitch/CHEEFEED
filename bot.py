# -*- coding: utf-8 -*-
import os
import re
import json
import socket
import asyncio
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlparse, quote
from google import genai

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

CHANNELS = [
    "MTProtoProxies",
    "ProxyMTProto",
    "TelMTProto",
    "iMTProto",
    "tgproxy",
]

PATTERNS = [
    re.compile(r'https://t\.me/proxy\?[^\s<>"]+'),
    re.compile(r'tg://proxy\?[^\s<>"]+'),
    re.compile(r'https://t\.me/socks\?[^\s<>"]+'),
    re.compile(r'tg://socks\?[^\s<>"]+'),
]

NITTER_SERVERS = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.freedit.eu"
]

TWITTER_ACCOUNTS = ["clashreport", "Osint613", "Faytuks", "WarMonitors"]

IRAN_KEYWORDS = ["iran", "tehran", "irgc", "iranian", "persian"]
RELATED_KEYWORDS = [
    "israel", "idf", "tel aviv", "us", "usa", "centcom", "pentagon", 
    "american", "washington", "strike", "drone", "missile", "nuclear", 
    "sanctions", "military", "syria", "lebanon", "gaza", "houthis"
]

EXCLUDE_KEYWORDS = [
    "football", "soccer", "fifa", "match", "league", "actor", "cinema", 
    "movie", "wedding", "stadium", "coach", "cup", "wrestling"
]

POLITICAL_SEARCH_QUERY = 'Iran AND (Israel OR "United States" OR US OR military OR nuclear OR strike OR sanctions)'
NEWS_RSS_URL = f"https://news.google.com/rss/search?q={quote(POLITICAL_SEARCH_QUERY)}&hl=en-US&gl=US&ceid=US:en"

HISTORY_FILE = "sent_news.json"

def get_flag_emoji(country_code):
    if not country_code or len(country_code) != 2:
        return "🔌"
    country_code = country_code.upper()
    return chr(127397 + ord(country_code[0])) + chr(127397 + ord(country_code[1]))

def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd

def get_persian_date_digits(now):
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
    return f"{jy}/{jm:02d}/{jd:02d}"

async def process_and_translate_with_gemini(raw_text, source_hint="رسانه‌های بین‌المللی"):
    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY is missing!")
        return None

    prompt = (
        "تو ادمین یک کانال تلگرامی داغ و جنجالی هستی.\n"
        "این خبر انگلیسی رو بخون و فقط و فقط لبّ مطلب و عصاره اصلی خبر رو به زبان فارسی روان، ۱۰۰٪ محاوره‌ای (با شکسته‌نویسی خیابانی/تلگرامی) خلاصه و ترجمه کن.\n\n"
        f"متن خبر: \"{raw_text}\"\n\n"
        "قوانین بسیار مهم:\n"
        "۱. تمام کلمات کتابی و رسمی رو حذف کن (مثلاً به جای «ایالات متحده بیان نمود» بگو «آمریکا گفت»، یا به جای «می‌نمایند» بگو «می‌کنن»).\n"
        "۲. فقط لبّ مطلب خبر در ۱ جمله بسیار کوتاه و جذاب.\n"
        f"۳. حتماً در انتهای جمله دقیقاً بنویس: — به نقل از {source_hint}\n"
        "۴. هیچ ایموجی اضافه نکن."
    )

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        target_model = 'gemini-2.0-flash'
        
        for attempt in range(3):
            try:
                print(f"🔄 Requesting Gemini translation for top news (Attempt {attempt+1})...")
                response = client.models.generate_content(
                    model=target_model,
                    contents=prompt
                )
                if response and response.text:
                    cleaned_text = response.text.strip().replace('"', '')
                    print(f"✨ Gemini Output: {cleaned_text}")
                    return cleaned_text
            except Exception as e:
                err_str = str(e)
                print(f"⚠️ Gemini Model Error: {err_str}")
                
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    wait_match = re.search(r'retry in (\d+\.?\d*)s', err_str)
                    wait_time = float(wait_match.group(1)) + 1.0 if wait_match else 7.0
                    print(f"⏳ Rate limit hit. Backing off for {wait_time:.1f} seconds...")
                    await asyncio.sleep(wait_time)
                else:
                    break

    except Exception as err:
        print(f"❌ Gemini Client Init Error: {err}")

    return None

def parse_pub_date(pub_date_str):
    if not pub_date_str:
        return datetime.min.replace(tzinfo=ZoneInfo("UTC"))
    try:
        return parsedate_to_datetime(pub_date_str)
    except:
        return datetime.min.replace(tzinfo=ZoneInfo("UTC"))

def extract_media_and_url(description, fallback_link):
    media_url = None
    media_type = None

    video_match = re.search(r'<video[^>]+src=["\']([^"\']+)["\']', description)
    if not video_match:
        video_match = re.search(r'<source[^>]+src=["\']([^"\']+)["\']', description)
    
    if video_match:
        media_url = video_match.group(1)
        media_type = "video"
    else:
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', description)
        if img_match:
            media_url = img_match.group(1)
            media_type = "photo"

    urls = re.findall(r'https?://[^\s<>"]+', description)
    source_url = fallback_link
    for url in urls:
        if not any(x in url for x in ["twitter.com", "x.com", "nitter"]):
            source_url = url
            break

    return media_url, media_type, source_url

async def fetch_all_news_candidates(sent_history):
    candidates = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        # ۱. جمع‌آوری اخبار از توییتر (Nitter)
        for server in NITTER_SERVERS:
            for acc in TWITTER_ACCOUNTS:
                try:
                    r = await client.get(f"{server}/{acc}/rss", headers=headers)
                    if r.status_code == 200:
                        root = ET.fromstring(r.text)
                        items = root.findall("./channel/item")
                        for item in items[:5]:
                            title = item.find("title").text if item.find("title") is not None else ""
                            description = item.find("description").text if item.find("description") is not None else ""
                            link = item.find("link").text if item.find("link") is not None else ""
                            pub_date_str = item.find("pubDate").text if item.find("pubDate") is not None else ""
                            
                            twitter_link = re.sub(r'https?://[^/]+', 'https://x.com', link)
                            media_url, media_type, final_source_url = extract_media_and_url(description, twitter_link)
                            
                            if media_url and media_url.startswith("/"):
                                media_url = f"{server}{media_url}"

                            full_text = f"{title} {description}".lower()

                            if any(ex in full_text for ex in EXCLUDE_KEYWORDS):
                                continue

                            has_iran = any(kw in full_text for kw in IRAN_KEYWORDS)
                            has_related = any(kw in full_text for kw in RELATED_KEYWORDS)

                            if (has_iran or has_related) and title not in sent_history:
                                candidates.append({
                                    "raw_title": f"{title}\n{description}",
                                    "link": final_source_url,
                                    "pub_date": parse_pub_date(pub_date_str),
                                    "source_name": f"توییتر (@{acc})",
                                    "media_url": media_url,
                                    "media_type": media_type
                                })
                except Exception:
                    pass

        # ۲. جمع‌آوری اخبار از Google News
        try:
            r = await client.get(NEWS_RSS_URL, headers=headers)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                items = root.findall("./channel/item")
                for item in items[:7]:
                    title = item.find("title").text if item.find("title") is not None else ""
                    link = item.find("link").text if item.find("link") is not None else ""
                    pub_date_str = item.find("pubDate").text if item.find("pubDate") is not None else ""
                    clean_title = re.sub(r'\s*-\s*[^-]+$', '', title).strip()

                    lower_title = clean_title.lower()
                    if any(ex in lower_title for ex in EXCLUDE_KEYWORDS):
                        continue

                    if clean_title and clean_title not in sent_history:
                        candidates.append({
                            "raw_title": clean_title,
                            "link": link,
                            "pub_date": parse_pub_date(pub_date_str),
                            "source_name": "رسانه‌های بین‌المللی",
                            "media_url": None,
                            "media_type": None
                        })
        except Exception as e:
            print(f"Google News error: {e}")

    if not candidates:
        print("ℹ️ No new news candidates found.")
        return None

    # ۳. مرتب‌سازی بر اساس تازه‌ترین تاریخ انتشار
    candidates.sort(key=lambda x: x["pub_date"], reverse=True)

    # 🎯 ارسال فقط ۱ خبر (جدیدترین خبر لیست) به Gemini
    top_candidate = candidates[0]
    print(f"🔥 Selected Top News from [{top_candidate['source_name']}]: {top_candidate['raw_title'][:60]}...")

    chatty_title = await process_and_translate_with_gemini(
        top_candidate["raw_title"], 
        source_hint=top_candidate["source_name"]
    )

    if chatty_title:
        return {
            "title": chatty_title,
            "link": top_candidate["link"],
            "raw": top_candidate["raw_title"],
            "media_url": top_candidate["media_url"],
            "media_type": top_candidate["media_type"]
        }

    return None

async def get_latest_important_news():
    sent_history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                sent_history = json.load(f)
        except:
            sent_history = []

    news = await fetch_all_news_candidates(sent_history)

    if news:
        sent_history.append(news["raw"])
        if len(sent_history) > 100:
            sent_history = sent_history[-100:]
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(sent_history, f, ensure_ascii=False)
        return news

    return None

async def check_proxy_and_get_country(client, host, port, timeout=3):
    try:
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        await asyncio.wait_for(
            loop.sock_connect(sock, (host, int(port))),
            timeout=timeout
        )
        sock.close()
        
        flag = "🔌"
        try:
            res = await client.get(f"http://ip-api.com/json/{host}?fields=countryCode", timeout=2)
            if res.status_code == 200:
                cc = res.json().get("countryCode")
                flag = get_flag_emoji(cc)
        except:
            pass

        return True, flag
    except:
        return False, "🔌"

def parse_proxy_url(url):
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        server = params.get('server', [None])[0]
        port = params.get('port', [None])[0]
        if server and port:
            return server, int(port)
    except:
        pass
    return None, None

async def scrape_proxies():
    found = set()
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for ch in CHANNELS:
            try:
                r = await client.get(f"https://t.me/s/{ch}")
                if r.status_code == 200:
                    for pattern in PATTERNS:
                        matches = pattern.findall(r.text)
                        for m in matches:
                            clean_url = m.replace("&amp;", "&")
                            found.add(clean_url)
            except Exception as e:
                print(f"Error scraping {ch}: {e}")
    return list(found)

async def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Error: BOT_TOKEN or CHAT_ID missing!")
        return

    print("🔎 Searching for Telegram proxies...")
    raw_proxies = await scrape_proxies()
    working_proxies = []
    
    async with httpx.AsyncClient() as http_client:
        for p_url in raw_proxies:
            server, port = parse_proxy_url(p_url)
            if server and port:
                is_ok, flag = await check_proxy_and_get_country(http_client, server, port)
                if is_ok:
                    working_proxies.append({"url": p_url, "flag": flag})
                    if len(working_proxies) >= 10:
                        break

    if not working_proxies:
        print("❌ No working proxies found.")
        return

    news = await get_latest_important_news()
    if not news:
        print("⚠️ No valid news translated by Gemini in this run.")
        return

    now_tehran = datetime.now(ZoneInfo("Asia/Tehran"))
    shamsi_date = get_persian_date_digits(now_tehran)
    gregorian_date = now_tehran.strftime("%Y/%m/%d")
    time_str = now_tehran.strftime("%H:%M")

    channel_clean = CHAT_ID.replace('@', '')
    channel_handle = f"@{channel_clean}"
    channel_url = f"https://t.me/{channel_clean}"

    msg = (
        f"⏰ <code>{time_str}</code>\n"
        f"🇮🇷 <code>{shamsi_date}</code>\n"
        f"🇺🇸 <code>{gregorian_date}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{news['title']}</b>\n\n"
        f"🔗 <a href='{news['link']}'>لینک خبر</a>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"اخبار یک‌خطی + پروکسی: <a href='{channel_url}'><b>{channel_handle}</b></a>"
    )

    keyboard_inline = []
    row = []
    for idx, proxy in enumerate(working_proxies, 1):
        button = {"text": f"{proxy['flag']} Proxy {idx:02d}", "url": proxy['url']}
        row.append(button)
        if len(row) == 2:
            keyboard_inline.append(row)
            row = []
            
    if row:
        keyboard_inline.append(row)

    reply_markup = {"inline_keyboard": keyboard_inline}

    async with httpx.AsyncClient(timeout=30) as client:
        media_sent = False
        
        if news.get("media_url") and news.get("media_type"):
            media_type = news["media_type"]
            endpoint = "sendPhoto" if media_type == "photo" else "sendVideo"
            param_key = "photo" if media_type == "photo" else "video"
            
            telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}"
            payload = {
                "chat_id": CHAT_ID,
                param_key: news["media_url"],
                "caption": msg,
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
            
            r = await client.post(telegram_url, json=payload)
            if r.status_code == 200:
                print(f"🚀 Message with {media_type} sent successfully!")
                media_sent = True

        if not media_sent:
            telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": reply_markup,
                "disable_web_page_preview": True
            }
            r = await client.post(telegram_url, json=payload)
            if r.status_code == 200:
                print("🚀 Text message sent successfully!")

if __name__ == "__main__":
    asyncio.run(main())
