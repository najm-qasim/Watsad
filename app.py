import os
import re
import unicodedata

import requests
import psycopg2
from flask import Flask, render_template_string, request


# ============================================================
# إعدادات Telegram
# ============================================================

# ضعها في Environment Variables داخل Render
#
# TELEGRAM_BOT_TOKEN
# TELEGRAM_CHAT_ID

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# ============================================================
# إعدادات PostgreSQL
# ============================================================

# Render يوفر غالبًا DATABASE_URL
# تلقائيًا عند ربط قاعدة PostgreSQL بالخدمة.

DATABASE_URL = os.environ.get("DATABASE_URL", "")


# ============================================================
# Flask
# ============================================================

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 16 * 1024


# ============================================================
# صفحة التسجيل
# ============================================================

PAGE = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#102a43">

  <title>تسجيل المتسابقين</title>

  <style>

    :root {
      color-scheme: light;
    }

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


# ============================================================
# الاتصال بقاعدة البيانات
# ============================================================

def get_db_connection():
    """
    إنشاء اتصال جديد بقاعدة PostgreSQL.
    """

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL غير موجود"
        )

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )


# ============================================================
# إنشاء جدول المتسابقين
# ============================================================

def init_database():
    """
    إنشاء جدول المتسابقين إذا لم يكن موجودًا.
    """

    connection = get_db_connection()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contestants (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        connection.commit()

        cursor.close()

    finally:

        connection.close()


# ============================================================
# توحيد الاسم
# ============================================================

def normalize_name(name: str) -> str:
    """
    تجهيز الاسم للمقارنة ومنع التكرار.

    مثال:

    "  أحمد   محمد  "

    تصبح:

    "أحمد محمد"
    """

    name = re.sub(
        r"\s+",
        " ",
        name.strip()
    )

    name = unicodedata.normalize(
        "NFC",
        name
    )

    # casefold مفيد خصوصًا إذا احتوى الاسم
    # على أحرف إنجليزية.
    name = name.casefold()

    return name


# ============================================================
# إرسال رسالة Telegram
# ============================================================

def send_to_telegram(
    number: int,
    name: str
) -> None:

    if not BOT_TOKEN or not CHAT_ID:

        raise RuntimeError(
            "إعدادات تيليجرام غير مكتملة"
        )


    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )


    response = requests.post(

        url,

        json={
            "chat_id": CHAT_ID,
            "text": f"{number} - {name}"
        },

        timeout=10
    )


    response.raise_for_status()


    result = response.json()


    if not result.get("ok"):

        raise RuntimeError(
            "رفض تيليجرام إرسال الرسالة"
        )


# ============================================================
# تسجيل المتسابق
# ============================================================

def register_contestant(name: str):
    """
    تسجيل المتسابق بطريقة آمنة.

    الخطوات:

    1. التحقق من عدم وجود الاسم.
    2. إنشاء رقم للمتسابق.
    3. إرسال الاسم إلى Telegram.
    4. حفظ التسجيل في PostgreSQL.

    إذا كان الاسم موجودًا مسبقًا:
    لا يتم إرسال Telegram
    ولا يتم إنشاء رقم جديد.
    """

    connection = get_db_connection()

    try:

        # بدء Transaction
        connection.autocommit = False

        cursor = connection.cursor()


        normalized_name = normalize_name(name)


        # ----------------------------------------------------
        # البحث عن الاسم
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT id
            FROM contestants
            WHERE normalized_name = %s
            LIMIT 1
            """,
            (normalized_name,)
        )


        existing = cursor.fetchone()


        if existing:

            connection.rollback()

            return {
                "success": False,
                "duplicate": True,
                "number": None
            }


        # ----------------------------------------------------
        # الحصول على الرقم التالي
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT COALESCE(
                MAX(id),
                0
            ) + 1
            FROM contestants
            """
        )


        row = cursor.fetchone()

        number = int(row[0])


        # ----------------------------------------------------
        # إرسال Telegram
        # ----------------------------------------------------

        send_to_telegram(
            number,
            name
        )


        # ----------------------------------------------------
        # حفظ المتسابق
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO contestants (
                id,
                name,
                normalized_name
            )
            VALUES (
                %s,
                %s,
                %s
            )
            """,
            (
                number,
                name,
                normalized_name
            )
        )


        connection.commit()


        return {
            "success": True,
            "duplicate": False,
            "number": number
        }


    except Exception:

        connection.rollback()

        raise


    finally:

        connection.close()


# ============================================================
# الصفحة الرئيسية
# ============================================================

@app.route(
    "/",
    methods=["GET", "POST"]
)
def register():

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    if request.method == "GET":

        return render_template_string(
            PAGE,
            success=False,
            error=None
        )


    # --------------------------------------------------------
    # الحصول على الاسم
    # --------------------------------------------------------

    name = request.form.get(
        "name",
        ""
    )


    # تنظيف المسافات
    name = re.sub(
        r"\s+",
        " ",
        name.strip()
    )


    # --------------------------------------------------------
    # التحقق من الاسم
    # --------------------------------------------------------

    if len(name) < 2 or len(name) > 80:

        return render_template_string(
            PAGE,
            success=False,
            error=(
                "يرجى إدخال اسم يتراوح "
                "بين حرفين و80 حرفًا."
            )
        ), 400


    # --------------------------------------------------------
    # تسجيل المتسابق
    # --------------------------------------------------------

    try:

        result = register_contestant(
            name
        )


        # ----------------------------------------------------
        # الاسم مكرر
        # ----------------------------------------------------

        if result["duplicate"]:

            return render_template_string(
                PAGE,
                success=False,
                error=(
                    "عذراً، هذا الاسم مسجل مسبقاً، "
                    "يرجى إدخال اسم آخر."
                )
            ), 409


        # ----------------------------------------------------
        # نجاح
        # ----------------------------------------------------

        return render_template_string(
            PAGE,
            success=True,
            error=None
        )


    # --------------------------------------------------------
    # خطأ Telegram
    # --------------------------------------------------------

    except requests.RequestException:

        app.logger.exception(
            "تعذر الاتصال بواجهة Telegram API"
        )


        return render_template_string(
            PAGE,
            success=False,
            error=(
                "تعذّر إرسال التسجيل الآن. "
                "يرجى المحاولة مرة أخرى بعد قليل."
            )
        ), 502


    # --------------------------------------------------------
    # خطأ الإعدادات
    # --------------------------------------------------------

    except RuntimeError as exc:

        app.logger.error(
            "Configuration error: %s",
            exc
        )


        return render_template_string(
            PAGE,
            success=False,
            error=(
                "خدمة التسجيل غير مكتملة الإعداد. "
                "يرجى التواصل مع المنظّمين."
            )
        ), 503


    # --------------------------------------------------------
    # خطأ قاعدة البيانات
    # --------------------------------------------------------

    except Exception:

        app.logger.exception(
            "Database/registration error"
        )


        return render_template_string(
            PAGE,
            success=False,
            error=(
                "حدث خطأ أثناء معالجة التسجيل. "
                "يرجى المحاولة مرة أخرى بعد قليل."
            )
        ), 500


# ============================================================
# تشغيل التطبيق
# ============================================================

if __name__ == "__main__":

    # إنشاء جدول قاعدة البيانات عند تشغيل التطبيق.

    try:

        init_database()

        print(
            "Database initialized successfully."
        )

    except Exception as exc:

        print(
            "Database initialization failed:",
            exc
        )


    # للتطوير المحلي فقط.
    # Render يستخدم Gunicorn.

    app.run(

        host="0.0.0.0",

        port=int(
            os.environ.get(
                "PORT",
                "5000"
            )
        ),

        debug=False
)
