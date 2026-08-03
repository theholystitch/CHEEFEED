import os
import time
from google import genai
from google.genai.errors import APIError

# مقداردهی اولیه کلاینت گوگل جن‌آی (مطمئن شوید کلید GEMINI_API_KEY در متغیرهای محیطی هاست تنظیم شده است)
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

def process_single_news_with_gemini(news_text):
    """
    تابع پردازش متن خبر با استفاده از مدل Flash-Lite جهت جلوگیری از خطای سهمیه (429)
    """
    prompt = f"لطفاً این متن را به یک سبک جذاب و خلاصه برای کانال تلگرام بازنویسی کن:\n\n{news_text}"
    
    max_retries = 3
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            # استفاده از مدل Flash-Lite برای بهینه‌سازی مصرف سهمیه و سرعت بالا
            response = client.models.generate_content(
                model='gemini-2.5-flash-lite',
                contents=prompt,
            )
            return response.text
            
        except APIError as e:
            # اگر خطای لیمیت (429) رخ داد
            if e.code == 429:
                print(f"هشدار: خطای لیمیت سهمیه (تلاش {attempt + 1} از {max_retries}). در حال استراحت...")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # افزایش زمان انتظار به صورت تصاعدی
                    continue
            print(APIError در ارتباط با هوش مصنوعی: {e})
            raise e
        except Exception as e:
            print(f"خطای ناشناخته: {e}")
            raise e

    return "خطا در پردازش خبر پس از چندین بار تلاش."

# مثال نحوه استفاده در ربات:
if __name__ == "__main__":
    sample_news = "خبر نمونه برای تست سیستم پردازش خودکار..."
    result = process_single_news_with_gemini(sample_news)
    print("نتیجه خروجی:")
    print(result)
