import os
import re

import requests
from flask import Flask, render_template_string, request


# إعدادات تيليجرام: عيّن هذين المتغيرين في بيئة التشغيل أو في Render.
# BOT_TOKEN: رمز البوت من BotFather
# CHAT_ID: معرّف المحادثة أو المجموعة التي ستستقبل التسجيلات
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ملف حفظ آخر رقم للمتسابق
COUNTER_FILE = "counter.txt"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024


PAGE = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#102a43">
  <title>تسجيل المتسابقين</title>

  <style>
    :root { color-scheme: light; }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      padding: 24px;

      display: grid;
      place-items: center;

      font-family: Tahoma, Arial, sans-serif;
      color: #102a43;

      background:
        radial-gradient(
          circle at 15% 15%,
          #d9f3ef 0,
          transparent 32%
        ),
        linear-gradient(
          135deg,
          #eef5fb,
          #f8fafc 55%,
          #e5eff7
        );
    }

    .card {
      width: min(100%, 470px);
      padding: clamp(25px, 7vw, 42px);

      background: #fff;
      border: 1px solid #e5edf4;
      border-radius: 22px;

      box-shadow:
        0 20px 60px rgba(16, 42, 67, .12);
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;

      padding: 8px 12px;
      border-radius: 999px;

      color: #087f70;
      background: #e6f7f3;

      font-size: 13px;
      font-weight: 700;
    }

    h1 {
      margin: 20px 0 10px;
      font-size: clamp(25px, 6vw, 32px);
    }

    .intro {
      margin: 0 0 26px;
      line-height: 1.8;
      color: #526779;
    }

    label {
      display: block;
      margin-bottom: 9px;

      font-size: 15px;
      font-weight: 700;
    }

    input {
      width: 100%;
      min-height: 52px;

      padding: 12px 15px;

      border: 1px solid #cbd8e3;
      border-radius: 12px;

      background: #fbfdff;
      color: #102a43;

      font: inherit;
      outline: none;

      transition:
        border-color .2s,
        box-shadow .2s;
    }

    input:focus {
      border-color: #168f82;
      box-shadow:
        0 0 0 4px rgba(22,143,130,.12);
    }

    button {
      width: 100%;
      min-height: 52px;

      margin-top: 16px;

      border: 0;
      border-radius: 12px;

      color: white;
      background: #087f70;

      font: inherit;
      font-weight: 700;

      cursor: pointer;

      transition:
        transform .15s,
        background .15s;
    }

    button:hover {
      background: #06695e;
    }

    button:active {
      transform: translateY(1px);
    }

    button:disabled {
      opacity: .65;
      cursor: wait;
    }

    .notice {
      margin: 0 0 20px;
      padding: 13px 15px;

      border-radius: 11px;
      line-height: 1.7;
    }

    .success {
      color: #17653d;
      background: #e8f7ed;
      border: 1px solid #b8e7c8;
    }

    .error {
      color: #9f2d2d;
      background: #fff0f0;
      border: 1px solid #f1c5c5;
    }

    .hint {
      margin: 13px 0 0;

      color: #718395;

      font-size: 12px;
      line-height: 1.7;
    }

    footer {
      margin-top: 24px;

      text-align: center;

      color: #8192a1;

      font-size: 12px;
    }
  </style>
</head>

<body>

  <main class="card">

    <span class="badge">
      ● التسجيل مفتوح
    </span>

    <h1>
      تسجيل المتسابقين
    </h1>

    <p class="intro">
      أدخل اسمك لإكمال التسجيل في المسابقة.
      سيصل التسجيل مباشرة إلى المنظّمين.
    </p>

    {% if success %}
      <div class="notice success" role="status">
        تم تسجيل اسمك بنجاح. بالتوفيق في المسابقة!
      </div>
    {% endif %}

    {% if error %}
      <div class="notice error" role="alert">
        {{ error }}
      </div>
    {% endif %}

    <form method="post" action="/" autocomplete="on">

      <label for="name">
        اسم المتسابق
      </label>

      <input
        id="name"
        name="name"
        type="text"
        maxlength="80"
        minlength="2"
        placeholder="اكتب الاسم الكامل"
        required
        autofocus
      >

      <button type="submit">
        إرسال التسجيل
      </button>

      <p class="hint">
        يرجى التأكد من كتابة الاسم بصورة صحيحة قبل الإرسال.
      </p>

    </form>

    <footer>
      نظام تسجيل المسابقة
    </footer>

  </main>

</body>
</html>
"""


def get_next_number():
    """الحصول على الرقم التالي وحفظه في ملف بسيط."""

    try:
        with open(COUNTER_FILE, "r", encoding="utf-8") as file:
            last_number = int(file.read().strip() or "0")
    except (FileNotFoundError, ValueError):
        last_number = 0

    next_number = last_number + 1

    with open(COUNTER_FILE, "w", encoding="utf-8") as file:
        file.write(str(next_number))

    return next_number


def send_to_telegram(number: int, name: str) -> None:
    """إرسال رقم المتسابق واسمه فقط إلى Telegram."""

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("إعدادات تيليجرام غير مكتملة")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": f"{number} - {name}"
        },
        timeout=10,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError("رفض تيليجرام إرسال الرسالة")


@app.route("/", methods=["GET", "POST"])
def register():

    if request.method == "GET":
        return render_template_string(
            PAGE,
            success=False,
            error=None
        )

    name = re.sub(
        r"\s+",
        " ",
        request.form.get("name", "").strip()
    )

    if len(name) < 2 or len(name) > 80:
        return render_template_string(
            PAGE,
            success=False,
            error="يرجى إدخال اسم يتراوح بين حرفين و80 حرفًا."
        ), 400

    try:
        number = get_next_number()

        send_to_telegram(
            number,
            name
        )

    except requests.RequestException:
        app.logger.exception(
            "تعذّر الاتصال بواجهة Telegram API"
        )

        return render_template_string(
            PAGE,
            success=False,
            error="تعذّر إرسال التسجيل الآن. يرجى المحاولة مرة أخرى بعد قليل."
        ), 502

    except RuntimeError as exc:
        app.logger.error(
            "Telegram configuration/API error: %s",
            exc
        )

        return render_template_string(
            PAGE,
            success=False,
            error="خدمة التسجيل غير مكتملة الإعداد. يرجى التواصل مع المنظّمين."
        ), 503

    return render_template_string(
        PAGE,
        success=True,
        error=None
    )


if __name__ == "__main__":
    # للتطوير المحلي فقط.
    # في Render شغّل التطبيق بواسطة Gunicorn.

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get("PORT", "5000")
        ),
        debug=False
) 
