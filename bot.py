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
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import gc
from google import genai

# ==================== متغیرهای محیطی ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
COHERE_API_KEY = os.getenv("COHERE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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

# ==================== لیست کامل منابع خبری ====================
NEWS_CHANNELS = {
    "persiannbloomberg": "Bloomberg فارسی",
    "presstv": "Press TV",
    "MiddleEastEye_TG": "Middle East Eye",
    "farsna": "فارس",
    "Tasnimnews": "تسنیم",
    "abdimedianet": "عبدی مدیا",
    "SharghDaily": "شرق",
    "euronewspe": "یورونیوز فارسی",
    "dw_farsi": "دویچه وله",
    "farsivoa": "صدای آمریکا",
    "entekhab_ir": "پایگاه خبری انتخاب",
    "Indypersian": "ایندیپندنت فارسی",
    "sahamnewsorg": "سهام نیوز",
    "radiofarda": "رادیو فردا",
    "hammihanonline": "روزنامه هم‌میهن",
    "EpochTimesPersian": "اپک تایمز"
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

# ==================== پردازش هوش مصنوعی ====================
async def process_single_news_with_ai(raw_text):
    raw_text_clean = re.sub(r'(?i)\b(just in|breaking|update):\s*', '', raw_text)
    raw_text_clean = re.sub(r'http\S+', '', raw_text_clean)
    raw_text_clean = re.sub(r'@\w+', '', raw_text_clean)
    raw_text_clean = re.sub(r'&[a-z0-9#]+;', ' ', raw_text_clean).strip()
    
    if not raw_text_clean:
        return None

    prompt = (
        "Analyze this news. FIRST, check if it is directly and primarily about shared news between **Iran and America**, **Iran and Israel**, or general events/wars involving **Iran**. "
        "If it is NOT related to these specific topics, reply with the exact word: IGNORE. "
        "If it IS related, follow these rules strictly:\n"
        "1. **Speaker Format:** If the news is a direct quote or statement from a specific person/official, format it strictly as: **نام شخص: متن خبر** (e.g., وزیر خارجه: متن). If it is a general report without a specific speaker, write just the **متن خبر** without any prefix. "
        "2. **Water Body Rule:** If you mention the Persian Gulf, you MUST write it fully and correctly as **خلیج فارس**. "
        "3. **Tone & Style:** Conversational, fluent, serious, and standard Persian (محاوره‌ای روان، جدی و با جمله‌بندی درست و استاندارد). No humor, jokes, or sarcasm. "
        "4. **Titles:** Do NOT use religious or restrictive honorifics; use clean direct names or standard job titles (e.g., وزیر خارجه, رئیس‌جمهور). "
        "5. **Length:** Keep it short. Prioritize a single line (تك‌خطی). If more detail is needed, use maximum 2 sentences. Ensure it is complete and clear, not cut off.\n"
        "CRITICAL: Output ONLY the final formatted Persian text or the word IGNORE."
        f"\n\nText: {raw_text_clean}"
    )

    async with httpx.AsyncClient(timeout=12) as client:
        if GROQ_API_KEY:
            try:
                res = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={"model": "deepseek-r1-distill-llama-70b", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2},
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
                )
                if res.status_code == 200:
                    text = res.json()["choices"][0]["message"]["content"].strip()
                    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip().replace('"', '')
                    if "IGNORE" not in text and len(text) >= 5:
                        return text
            except Exception:
                pass

        if DEEPSEEK_API_KEY:
            try:
                res = await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2},
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
                )
                if res.status_code == 200:
                    text = res.json()["choices"][0]["message"]["content"].strip().replace('"', '')
                    if "IGNORE" not in text and len(text) >= 5:
                        return text
            except Exception:
                pass

        if OPENROUTER_API_KEY:
            try:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json={"model": "openrouter/free", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2},
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json", "HTTP-Referer": "https://github.com", "X-Title": "Telegram Bot"}
                )
                if res.status_code == 200:
                    text = res.json()["choices"][0]["message"]["content"].strip().replace('"', '')
                    if "IGNORE" not in text and len(text) >= 5:
                        return text
            except Exception:
                pass

        if COHERE_API_KEY:
            try:
                res = await client.post(
                    "https://api.cohere.com/v2/chat",
                    json={"model": "command-r-plus", "messages": [{"role": "user", "content": prompt}]},
                    headers={"Authorization": f"Bearer {COHERE_API_KEY}", "Content-Type": "application/json"}
                )
                if res.status_code == 200:
                    text = res.json()["message"]["content"][0]["text"].strip().replace('"', '')
                    if "IGNORE" not in text and len(text) >= 5:
                        return text
            except Exception:
                pass

    if gemini_client:
        try:
            response = gemini_client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt,
            )
            text = response.text.strip().replace('"', '')
            if "IGNORE" not in text and len(text) >= 5:
                return text
        except Exception:
            pass

    return None

async def fetch_telegram_channel_news(channel_username, channel_display_name, sent_history):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    url = f"https://t.me/s/{channel_username}"

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        try:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                messages = r.text.split('tgme_widget_message_wrap')
                for message in reversed(messages[-8:]):
                    text_match = re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', message, re.DOTALL)
                    if not text_match:
                        continue
                    
                    clean_text = re.sub(r'<[^>]+>', ' ', text_match.group(1)).strip()
                    clean_text = re.sub(r'\s+', ' ', clean_text)
                    
                    if not clean_text or len(clean_text) < 20:
                        continue
                    if any(ex in clean_text.lower() for ex in EXCLUDE_KEYWORDS):
                        continue

                    raw_id = f"{channel_username}_{clean_text[:40]}"
                    if raw_id not in sent_history:
                        return {
                            "raw_text": clean_text,
                            "source_name": channel_display_name,
                            "raw_id": raw_id
                        }
        except Exception:
            pass
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

    # بررسی تک‌تک کانال‌ها تا پیدا کردن اولین خبر معتبر و مرتبط بدون معطلی
    for username, display_name in channels_list:
        candidate = await fetch_telegram_channel_news(username, display_name, sent_history)
        if candidate:
            ai_title = await process_single_news_with_ai(candidate["raw_text"])
            if ai_title and "Safety" not in ai_title:
                sent_history.append(candidate["raw_id"])
                if len(sent_history) > 300:
                    sent_history = sent_history[-300:]
                try:
                    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                        json.dump(sent_history, f, ensure_ascii=False)
                except:
                    pass

                return {
                    "title": ai_title,
                    "source": candidate["source_name"]
                }
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
            except Exception:
                pass
    return list(found)

async def job():
    if not BOT_TOKEN or not CHAT_ID:
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

    if not working_proxies:
        gc.collect()
        return

    news = await get_latest_important_news()
    if not news:
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
        try:
            telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": reply_markup,
                "disable_web_page_preview": True
            }
            await client.post(telegram_url, json=payload)
        except Exception:
            pass
    
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
