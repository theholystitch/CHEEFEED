# -*- coding: utf-8 -*-
import os
import re
import json
import socket
import asyncio
import random
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import gc
from deep_translator import GoogleTranslator

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

PROXY_CHANNELS = [
    "Proxy_Qavi",
    "ProxySkull",
]

PATTERNS = [
    re.compile(r'https://t\.me/proxy\?[^\s<>"]+'),
    re.compile(r'tg://proxy\?[^\s<>"]+'),
    re.compile(r'https://t\.me/socks\?[^\s<>"]+'),
    re.compile(r'tg://socks\?[^\s<>"]+'),
]

NEWS_CHANNELS = {
    "persiannbloomberg": "Bloomberg فارسی",
    "presstv": "Press TV",
    "MiddleEastEye_TG": "Middle East Eye"
}

EXCLUDE_KEYWORDS = [
    "football", "soccer", "fifa", "match", "league", "actor", "cinema", 
    "movie", "wedding", "stadium", "coach", "cup", "wrestling", "fashion", "music"
]

HISTORY_FILE = "sent_news.json"

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")

    def log_message(self, format, *args):
        return

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

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
    days = (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) - ((gy2 + 399) // 400) - 80 + gd + g_d_m[gm - 1]
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

async def process_and_translate_with_openrouter(raw_text):
    raw_text_clean = re.sub(r'(?i)\b(just in|breaking|update):\s*', '', raw_text)
    raw_text_clean = re.sub(r'http\S+', '', raw_text_clean)
    raw_text_clean = re.sub(r'@\w+', '', raw_text_clean)
    raw_text_clean = re.sub(r'&[a-z0-9#]+;', ' ', raw_text_clean).strip()
    
    if not raw_text_clean:
        return None

    # اولویت اول: OpenRouter
    if OPENROUTER_API_KEY:
        prompt = (
            "Analyze this news. FIRST, check if it is directly and primarily about **Iran** (or actions, attacks, threats, and negotiations involving Iran with the USA or Israel). "
            "If it is NOT primarily about Iran, reply with the exact word: IGNORE. "
            "If it IS related, rewrite it in **simple, direct, and concise Persian (محاوره‌ای ساده و خلاصه)**. "
            "Keep it factual and within 1 to 2 sentences so it's not too long. "
            "CRITICAL: If you mention the Persian Gulf, you MUST write it fully and correctly as **خلیج فارس**. "
            "CRITICAL: Output ONLY the Persian sentence or the word IGNORE."
            f"\n\nText: {raw_text_clean}"
        )

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com",
            "X-Title": "Telegram News Bot"
        }
        
        payload = {
            "model": "openrouter/free",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=payload, headers=headers)
                print(f"🤖 OpenRouter Status: {response.status_code}")
                if response.status_code == 200:
                    data = response.json()
                    text = data["choices"][0]["message"]["content"].strip().replace('"', '')
                    print(f"🤖 OpenRouter Output: {text}")
                    if "IGNORE" in text or len(text) < 5:
                        return None
                    return text
                else:
                    print(f"⚠️ OpenRouter Error/Limit: {response.text} -> Falling back to structured translation")
        except Exception as e:
            print(f"❌ OpenRouter Exception: {e} -> Falling back to structured translation")

    # فیلتر کلمات کلیدی برای ایران
    keywords_to_check = ["iran", "tehran", "israel", "usa", "us ", "america", "persian"]
    text_lower = raw_text_clean.lower()
    
    if not any(kw in text_lower for kw in keywords_to_check):
        print("⚠️ News not related to Iran keywords, ignoring.")
        return None

    # اولویت دوم (پشتیبان): ترجمه جمله‌به‌جمله و ساختاریافته برای جلوگیری از به‌هم‌ریختگی
    try:
        sentences = re.split(r'(?<=[.!?])\s+', raw_text_clean)
        translated_sentences = []
        translator = GoogleTranslator(source='auto', target='fa')
        
        for s in sentences[:2]: # فقط دو جمله اول برای جلوگیری از طولانی شدن
            s = s.strip()
            if len(s) > 3:
                tr = translator.translate(s)
                if tr:
                    tr = re.sub(r'(?i)persian gulf', 'خلیج فارس', tr)
                    translated_sentences.append(tr.strip())

        if translated_sentences:
            final_text = " ".join(translated_sentences)
            final_text = re.sub(r'&[a-z0-9#]+;', ' ', final_text)
            final_text = re.sub(r'@\w+', '', final_text)
            final_text = re.sub(r'\s+', ' ', final_text).strip()
            
            print(f"🌐 Structured Fallback Output: {final_text}")
            return final_text
    except Exception as e:
        print(f"❌ Translation Fallback Error: {e}")

    return None

async def fetch_telegram_channel_news(channel_username, channel_display_name, sent_history):
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"https://t.me/s/{channel_username}"

    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        try:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                messages = r.text.split('tgme_widget_message_wrap')
                for message in reversed(messages[-3:]):
                    photo_url = None
                    img_match = re.search(r'background-image:\s*url\(\'(.*?)\'\)', message)
                    if img_match:
                        candidate_url = img_match.group(1)
                        if candidate_url.startswith('http'):
                            photo_url = candidate_url

                    text_match = re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', message, re.DOTALL)
                    if not text_match:
                        continue
                    
                    clean_text = re.sub(r'<[^>]+>', ' ', text_match.group(1)).strip()
                    clean_text = re.sub(r'\s+', ' ', clean_text)
                    if not clean_text or len(clean_text) < 15:
                        continue
                    if any(ex in clean_text.lower() for ex in EXCLUDE_KEYWORDS):
                        continue

                    raw_id = f"{channel_username}_{clean_text[:30]}"
                    if raw_id not in sent_history:
                        print(f"📰 Found new news from {channel_display_name}")
                        return {
                            "raw_text": clean_text,
                            "source_name": channel_display_name,
                            "raw_id": raw_id,
                            "photo_url": photo_url
                        }
        except Exception as e:
            print(f"❌ Error fetching channel {channel_username}: {e}")
    return None

async def get_latest_important_news():
    sent_history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                sent_history = json.load(f)
        except:
            sent_history = []

    channels_list = list(NEWS_CHANNELS.items())
    random.shuffle(channels_list)

    for username, display_name in channels_list:
        candidate = await fetch_telegram_channel_news(username, display_name, sent_history)
        if candidate:
            chatty_title = await process_and_translate_with_openrouter(candidate["raw_text"])
            if chatty_title and "Safety" not in chatty_title:
                sent_history.append(candidate["raw_id"])
                if len(sent_history) > 200:
                    sent_history = sent_history[-200:]
                try:
                    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                        json.dump(sent_history, f, ensure_ascii=False)
                except:
                    pass

                return {
                    "title": chatty_title,
                    "source": candidate["source_name"],
                    "photo_url": candidate.get("photo_url")
                }
    print("⚠️ No valid news passed filters or all were duplicates.")
    return None

async def check_proxy_and_get_country(client, host, port, timeout=2):
    try:
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        await asyncio.wait_for(loop.sock_connect(sock, (host, int(port))), timeout=timeout)
        sock.close()
        
        flag = "🔌"
        try:
            res = await client.get(f"http://ip-api.com/json/{host}?fields=countryCode", timeout=1.5)
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
    async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for ch in PROXY_CHANNELS:
            try:
                r = await client.get(f"https://t.me/s/{ch}")
                if r.status_code == 200:
                    for pattern in PATTERNS:
                        matches = pattern.findall(r.text)
                        for m in matches:
                            clean_url = m.replace("&amp;", "&")
                            found.add(clean_url)
            except Exception as e:
                print(f"❌ Error scraping proxy channel {ch}: {e}")
    print(f"🔌 Total proxies found: {len(found)}")
    return list(found)

async def job():
    print("🚀 Job started...")
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Error: BOT_TOKEN or CHAT_ID is missing!")
        return

    raw_proxies = await scrape_proxies()
    working_proxies = []
    
    async with httpx.AsyncClient() as http_client:
        for p_url in raw_proxies:
            server, port = parse_proxy_url(p_url)
            if server and port:
                is_ok, flag = await check_proxy_and_get_country(http_client, server, port)
                if is_ok:
                    working_proxies.append({"url": p_url, "flag": flag})
                    if len(working_proxies) >= 5:
                        break

    print(f"🟢 Working proxies found: {len(working_proxies)}")
    if not working_proxies:
        print("⚠️ No working proxies found, skipping this cycle.")
        gc.collect()
        return

    news = await get_latest_important_news()
    if not news:
        print("⚠️ No news available to send, skipping this cycle.")
        gc.collect()
        return

    now_tehran = datetime.now(ZoneInfo("Asia/Tehran"))
    shamsi_date = get_persian_date_digits(now_tehran)
    gregorian_date = now_tehran.strftime("%Y/%m/%d")
    time_str = now_tehran.strftime("%H:%M")

    channel_handle = "@Dickonnect"
    channel_url = "https://t.me/Dickonnect"

    msg = (
        f"⏰ <code>{time_str}</code>\n"
        f"🇮🇷 <code>{shamsi_date}</code>\n"
        f"🇺🇸 <code>{gregorian_date}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{news['title']}</b>\n\n"
        f"🌐 <b>منبع:</b> {news['source']}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📢 <a href='{channel_url}'><b>{channel_handle}</b></a> اخبار تک‌خطی + پروکسی"
    )

    keyboard_inline = []
    row = []
    for idx, proxy in enumerate(working_proxies, 1):
        button = {"text": f"{proxy['flag']} پروکسی #{idx}", "url": proxy['url']}
        row.append(button)
        if len(working_proxies) == 5:
            if len(row) == 2 and len(keyboard_inline) < 2:
                keyboard_inline.append(row)
                row = []
            elif len(row) == 1 and len(keyboard_inline) == 2:
                keyboard_inline.append(row)
                row = []
        else:
            if len(row) == 2:
                keyboard_inline.append(row)
                row = []
    if row:
        keyboard_inline.append(row)

    keyboard_inline.append([
        {"text": "✨ عضویت در کانال Dickonnect", "url": channel_url}
    ])

    reply_markup = {"inline_keyboard": keyboard_inline}

    async with httpx.AsyncClient(timeout=15) as client:
        success = False
        if news.get("photo_url"):
            try:
                telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                payload = {
                    "chat_id": CHAT_ID,
                    "photo": news["photo_url"],
                    "caption": msg,
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup
                }
                res = await client.post(telegram_url, json=payload)
                print(f"📤 SendPhoto Telegram status: {res.status_code}")
                if res.status_code == 200:
                    success = True
                else:
                    print(f"⚠️ SendPhoto Error Response: {res.text}")
            except Exception as e:
                print(f"❌ SendPhoto Exception: {e}")

        if not success:
            try:
                telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": CHAT_ID,
                    "text": msg,
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup,
                    "disable_web_page_preview": True
                }
                res = await client.post(telegram_url, json=payload)
                print(f"📤 SendMessage Telegram status: {res.status_code}")
                if res.status_code != 200:
                    print(f"❌ SendMessage Error Response: {res.text}")
            except Exception as e:
                print(f"❌ SendMessage Exception: {e}")
    
    gc.collect()

async def main():
    server_thread = threading.Thread(target=run_dummy_server, daemon=True)
    server_thread.start()
    
    asyncio.create_task(job())

    while True:
        try:
            await job()
        except Exception as e:
            print(f"❌ Main Loop Error: {e}")
        gc.collect()
        await asyncio.sleep(900)

if __name__ == "__main__":
    asyncio.run(main())

