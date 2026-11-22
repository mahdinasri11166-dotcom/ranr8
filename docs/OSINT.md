# OSINT Tools — GGUF Runner

ابزارهای جمع‌آوری اطلاعات از **منابع عمومی** برای تحقیق دفاعی / تحلیل امنیتی.

## فعال‌سازی

- پروفایل `osint` را انتخاب کنید، یا
- سوییچ **OSINT focus** + **Agent mode**، یا
- دستور: `/osint on`

گزارش سریع بدون مدل:
```
/osint example.com
/osint 8.8.8.8
/osint user@domain.com
/osint someusername
/osint https://example.com
```

## ابزارها

| ابزار | توضیح |
|--------|--------|
| `osint_report` | تشخیص نوع هدف و اجرای بستهٔ اولیه |
| `osint_domain` | DNS + گواهی TLS + پروب HTTP |
| `osint_dns` | Resolve DNS |
| `osint_ip` | موقعیت تقریبی / ASN (ip-api) |
| `osint_username` | بررسی حضور عمومی در پلتفرم‌ها |
| `osint_email` | اعتبار قالب + DNS دامنه |
| `osint_search` | جستجوی DuckDuckGo HTML |
| `osint_fetch` | دریافت صفحه + استخراج ایمیل/لینک |
| `osint_wayback` | اسنپ‌شات Internet Archive |
| `osint_github_user` / `osint_github_repo` | API عمومی گیت‌هاب |
| `osint_extract` | استخراج ایمیل/URL/تلفن از متن |
| `save_osint_report` | ذخیره JSON در `workspace/osint/` |

## محدودیت‌ها و اخلاق

- بدون brute-force، بدون ورود به حساب، بدون scrape خصوصی
- نتایج username فقط **حدس از HTTP عمومی** است
- قوانین محلی و ToS پلتفرم‌ها را رعایت کنید
- برای استفادهٔ تحقیقاتی / دفاعی طراحی شده است

## Approval Gate

ابزارهای شبکه‌ای حساس‌تر (search, fetch, username, report) نیاز به تأیید دارند.
