import hmac
import logging
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template_string, request

# إعدادات البوت من متغيرات البيئة.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
# اختياري: ضع Telegram user ID الخاص بك لمنع الآخرين في المجموعة من اعتماد التسجيل.
TELEGRAM_ADMIN_USER_ID = os.environ.get("TELEGRAM_ADMIN_USER_ID", "").strip()

# ملفات التسجيل المحلية، وتُحفظ بجانب app.py.
NAMES_FILE = "names.txt"
COUNTER_FILE = "counter.txt"
APP_DIR = Path(__file__).resolve().parent
NAMES_PATH = APP_DIR / NAMES_FILE
COUNTER_PATH = APP_DIR / COUNTER_FILE
LOCK_PATH = APP_DIR / ".registration.lock"

TELEGRAM_TIMEOUT_SECONDS = 10
MAX_NAME_LENGTH = 80
REVIEWED_PREFIX = "✅ تمت المراجعة\n\n"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
app.logger.setLevel(logging.INFO)

# قفل لمنع تضارب التسجيلات المتزامنة بين الخيوط وعمليات Gunicorn على Linux.
_THREAD_LOCK = threading.RLock()
try:
    import fcntl
except ImportError:  # توافق احتياطي مع الأنظمة غير الداعمة لـ POSIX
    fcntl = None

PAGE = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#102a43">
  <title>تسجيل المتسابقين</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; padding: 24px;
      display: grid; place-items: center;
      font-family: Tahoma, Arial, sans-serif; color: #102a43;
      background: radial-gradient(circle at 15% 15%, #d9f3ef 0, transparent 32%),
                  linear-gradient(135deg, #eef5fb, #f8fafc 55%, #e5eff7);
    }
    .card {
      width: min(100%, 480px); padding: clamp(25px, 7vw, 42px);
      background: #fff; border: 1px solid #e5edf4; border-radius: 22px;
      box-shadow: 0 20px 60px rgba(16, 42, 67, .12);
    }
    .badge {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 8px 12px; border-radius: 999px; color: #087f70;
      background: #e6f7f3; font-size: 13px; font-weight: 700;
    }
    h1 { margin: 20px 0 10px; font-size: clamp(25px, 6vw, 32px); }
    .intro { margin: 0 0 26px; line-height: 1.8; color: #526779; }
    label { display: block; margin-bottom: 9px; font-size: 15px; font-weight: 700; }
    input {
      width: 100%; min-height: 52px; padding: 12px 15px;
      border: 1px solid #cbd8e3; border-radius: 12px;
      background: #fbfdff; color: #102a43; font: inherit; outline: none;
      transition: border-color .2s, box-shadow .2s;
    }
    input:focus { border-color: #168f82; box-shadow: 0 0 0 4px rgba(22,143,130,.12); }
    button {
      width: 100%; min-height: 52px; margin-top: 16px; border: 0;
      border-radius: 12px; color: #fff; background: #087f70;
      font: inherit; font-weight: 700; cursor: pointer;
      transition: background .15s, transform .15s;
    }
    button:hover { background: #06695e; }
    button:active { transform: translateY(1px); }
    .message { margin: 0 0 20px; padding: 13px 15px; border-radius: 11px; line-height: 1.8; }
    .success { color: #17653d; background: #e8f7ed; border: 1px solid #b8e7c8; }
    .error { color: #9f2d2d; background: #fff0f0; border: 1px solid #f1c5c5; }
    .hint { margin: 13px 0 0; color: #718395; font-size: 12px; line-height: 1.7; }
    footer { margin-top: 24px; text-align: center; color: #8192a1; font-size: 12px; }
  </style>
</head>
<body>
  <main class="card">
    <span class="badge">● التسجيل مفتوح</span>
    <h1>تسجيل المتسابقين</h1>
    <p class="intro">أدخل اسمك لإكمال التسجيل في المسابقة.</p>
    {% if message %}
      <div class="message {{ message_type }}"
           role="{{ 'status' if message_type == 'success' else 'alert' }}">{{ message }}</div>
    {% endif %}
    <form method="post" action="/" autocomplete="on">
      <label for="name">اسم المتسابق</label>
      <input id="name" name="name" type="text" maxlength="80"
             placeholder="اكتب الاسم الكامل" required autofocus>
      <button type="submit">إرسال التسجيل</button>
      <p class="hint">يجب أن يتراوح الاسم بين حرفين و80 حرفًا.</p>
    </form>
    <footer>نظام تسجيل المسابقة</footer>
  </main>
</body>
</html>"""


class RegistrationError(Exception):
    """أخطاء متوقعة أثناء التسجيل أو التعامل مع الملفات."""


class TelegramError(RegistrationError):
    """يُرفع عند فشل Telegram Bot API."""


def telegram_api(method, payload):
    """استدعاء Telegram Bot API والتحقق من نجاح الاستجابة."""
    if not TELEGRAM_BOT_TOKEN:
        raise TelegramError("متغير TELEGRAM_BOT_TOKEN غير مضبوط.")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=TELEGRAM_TIMEOUT_SECONDS)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f"فشل طلب Telegram API: {method}") from exc

    if not isinstance(result, dict) or not result.get("ok"):
        description = result.get("description", "استجابة غير ناجحة") if isinstance(result, dict) else "استجابة غير صالحة"
        raise TelegramError(f"Telegram API {method}: {description}")

    return result.get("result")


def build_keyboard(name, contestant_number, reviewed=False):
    """زر نسخ الاسم وزر تبديل حالة المراجعة."""
    if reviewed:
        review_button = {
            "text": "✅ تمت المراجعة",
            "callback_data": f"done:{contestant_number}",
        }
    else:
        review_button = {
            "text": "☑️ تمت المراجعة",
            "callback_data": f"review:{contestant_number}",
        }

    return {
        "inline_keyboard": [
            [
                {
                    "text": "📋 نسخ الاسم",
                    "copy_text": {"text": name},
                }
            ],
            [review_button],
        ]
    }


def send_registration_message(name, contestant_number):
    """إرسال الاسم والرقم مع زري النسخ واعتماد المراجعة."""
    return telegram_api(
        "sendMessage",
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": f"{contestant_number} - {name}",
            "reply_markup": build_keyboard(name, contestant_number),
        },
    )


@contextmanager
def registration_lock():
    """قفل عمليات التسجيل المتزامنة عبر الخيوط وعمليات الخادم."""
    with _THREAD_LOCK:
        lock_file = None
        try:
            lock_file = open(LOCK_PATH, "a+", encoding="utf-8")
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if lock_file is not None:
                if fcntl is not None:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        app.logger.exception("تعذر تحرير قفل التسجيل")
                lock_file.close()


def read_registered_names():
    try:
        content = NAMES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [line.strip() for line in content.splitlines() if line.strip()]


def read_counter(number_of_names):
    try:
        raw_value = COUNTER_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return number_of_names

    if not raw_value:
        return number_of_names
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RegistrationError("ملف العداد يحتوي على قيمة غير صالحة.") from exc
    if value < 0:
        raise RegistrationError("ملف العداد يحتوي على قيمة غير صالحة.")
    return max(value, number_of_names)


def stage_text_file(destination, content):
    """إنشاء ملف مؤقت؛ لا يتغير الملف الأصلي قبل نجاح إرسال تيليجرام."""
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return Path(temporary_name)


def render_page(message=None, message_type=None, status=200):
    return render_template_string(
        PAGE, message=message, message_type=message_type
    ), status


def answer_callback_query(callback_query_id, text=None):
    """إيقاف مؤشر الانتظار الذي يعرضه Telegram بعد ضغط زر callback."""
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]
    telegram_api("answerCallbackQuery", payload)


def handle_review_callback(callback_query):
    """اعتماد المراجعة وتحديث الرسالة مع الإبقاء على زر نسخ الاسم."""
    callback_id = callback_query.get("id")
    data = callback_query.get("data", "")
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}

    if not callback_id or not isinstance(data, str):
        return

    # السماح بالتفاعل فقط داخل المحادثة التي تستقبل التسجيلات.
    if str(chat.get("id", "")) != TELEGRAM_CHAT_ID:
        answer_callback_query(callback_id, "هذه الرسالة ليست في محادثة التسجيل.")
        return

    # اختياري للمجموعات: حصر زر المراجعة على حساب Telegram واحد.
    if TELEGRAM_ADMIN_USER_ID:
        clicker_id = str((callback_query.get("from") or {}).get("id", ""))
        if clicker_id != TELEGRAM_ADMIN_USER_ID:
            answer_callback_query(callback_id, "هذا الزر مخصص للمسؤول فقط.")
            return

    match = re.fullmatch(r"(review|done):(\d+)", data)
    if not match:
        answer_callback_query(callback_id, "زر غير معروف.")
        return

    action, number = match.groups()
    message_text = message.get("text", "")

    if action == "done" or message_text.startswith(REVIEWED_PREFIX):
        answer_callback_query(callback_id, "تمت مراجعة هذا الاسم مسبقًا.")
        return

    # تحقق من أن callback يخص رسالة تسجيل بالرقم نفسه.
    registration_match = re.fullmatch(r"(\d+) - (.+)", message_text, flags=re.DOTALL)
    if not registration_match or registration_match.group(1) != number:
        answer_callback_query(callback_id, "تعذر التحقق من بيانات التسجيل.")
        return

    name = registration_match.group(2)
    try:
        # acknowledge الضغط أولًا كي لا يبقى مؤشر الانتظار ظاهرًا.
        answer_callback_query(callback_id, "تم اعتماد المراجعة.")
    except TelegramError:
        app.logger.exception("تعذر إظهار إشعار callback في تيليجرام")

    edited_text = f"{REVIEWED_PREFIX}{message_text}"
    try:
        telegram_api(
            "editMessageText",
            {
                "chat_id": chat["id"],
                "message_id": message["message_id"],
                "text": edited_text,
                "reply_markup": build_keyboard(name, int(number), reviewed=True),
            },
        )
    except (TelegramError, KeyError, TypeError):
        app.logger.exception("تعذر تحديث رسالة التسجيل بعد المراجعة")
        raise


@app.route("/", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_page()

    # إزالة المسافات الزائدة والمتكررة؛ الأقواس مكتملة عمدًا.
    name = re.sub(r"\s+", " ", request.form.get("name", "").strip())
    if not 2 <= len(name) <= MAX_NAME_LENGTH:
        return render_page(
            "يرجى إدخال اسم يتراوح بين حرفين و80 حرفًا.", "error", 400
        )

    staged_names = None
    staged_counter = None

    try:
        with registration_lock():
            registered_names = read_registered_names()
            existing_normalized = {registered.casefold() for registered in registered_names}

            if name.casefold() in existing_normalized:
                return render_page(
                    "عذراً، هذا الاسم مسجل مسبقاً، يرجى إدخال اسم آخر.",
                    "error",
                    409,
                )

            contestant_number = read_counter(len(registered_names)) + 1
            updated_names = registered_names + [name]
            names_content = "".join(f"{registered}\n" for registered in updated_names)
            counter_content = f"{contestant_number}\n"

            staged_names = stage_text_file(NAMES_PATH, names_content)
            staged_counter = stage_text_file(COUNTER_PATH, counter_content)

            # لا تُحفظ الملفات إلا بعد أن يؤكد Telegram نجاح الإرسال.
            send_registration_message(name, contestant_number)
            os.replace(staged_names, NAMES_PATH)
            staged_names = None
            os.replace(staged_counter, COUNTER_PATH)
            staged_counter = None

        return render_page(
            f"تم تسجيلك بنجاح! رقم المتسابق: {contestant_number}.",
            "success",
            200,
        )

    except TelegramError:
        app.logger.exception("فشل إرسال التسجيل إلى تيليجرام")
        return render_page(
            "تعذر إرسال التسجيل إلى تيليجرام. لم يُحفظ الاسم؛ يرجى المحاولة لاحقًا.",
            "error",
            502,
        )
    except (OSError, UnicodeError, RegistrationError):
        app.logger.exception("خطأ في ملفات التسجيل أو إعداداته")
        return render_page(
            "حدث خطأ أثناء حفظ التسجيل. يرجى المحاولة لاحقًا.", "error", 500
        )
    except Exception:
        app.logger.exception("خطأ غير متوقع أثناء التسجيل")
        return render_page(
            "حدث خطأ غير متوقع. يرجى المحاولة لاحقًا.", "error", 500
        )
    finally:
        for staged_file in (staged_names, staged_counter):
            if staged_file is not None:
                try:
                    staged_file.unlink(missing_ok=True)
                except OSError:
                    app.logger.exception("تعذر حذف ملف مؤقت للتسجيل")


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    """استقبال ضغط زر «تمت المراجعة» من Telegram عبر Webhook."""
    if not TELEGRAM_WEBHOOK_SECRET:
        app.logger.error("TELEGRAM_WEBHOOK_SECRET غير مضبوط")
        return jsonify(ok=False, error="webhook_not_configured"), 503

    supplied_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(supplied_secret, TELEGRAM_WEBHOOK_SECRET):
        return jsonify(ok=False, error="unauthorized"), 403

    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return jsonify(ok=False, error="invalid_update"), 400

    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        # تجاهل تحديثات تيليجرام الأخرى مثل الرسائل العادية.
        return jsonify(ok=True), 200

    try:
        handle_review_callback(callback_query)
    except TelegramError:
        # 5xx يجعل Telegram يعيد إرسال الـ update عند الفشل المؤقت.
        return jsonify(ok=False, error="telegram_api_error"), 500
    except Exception:
        app.logger.exception("فشل معالجة Telegram callback")
        return jsonify(ok=False, error="callback_processing_error"), 500

    return jsonify(ok=True), 200


if __name__ == "__main__":
    # للتشغيل المحلي فقط. في Render استخدم أمر التشغيل: gunicorn app:app
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
    )
