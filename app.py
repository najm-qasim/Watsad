import hmac
import hashlib
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
# اختياري: يحصر اعتماد التسجيل على حساب Telegram واحد.
TELEGRAM_ADMIN_USER_ID = os.environ.get("TELEGRAM_ADMIN_USER_ID", "").strip()

# ملفات البيانات المحلية بجانب app.py.
NAMES_FILE = "names.txt"
COUNTER_FILE = "counter.txt"
APPROVED_FILE = "approved.txt"
DEVICES_FILE = "devices.txt"
APP_DIR = Path(__file__).resolve().parent
NAMES_PATH = APP_DIR / NAMES_FILE
COUNTER_PATH = APP_DIR / COUNTER_FILE
APPROVED_PATH = APP_DIR / APPROVED_FILE
DEVICES_PATH = APP_DIR / DEVICES_FILE
LOCK_PATH = APP_DIR / ".registration.lock"

TELEGRAM_TIMEOUT_SECONDS = 10
MAX_NAME_LENGTH = 80
REVIEWED_PREFIX = "✅ تمت المراجعة\n\n"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
app.logger.setLevel(logging.INFO)

# قفل لمنع تضارب الملفات بين الخيوط وعمليات Gunicorn على Linux.
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
    button:disabled { opacity: .65; cursor: wait; }
    .message { margin: 0 0 20px; padding: 13px 15px; border-radius: 11px; line-height: 1.8; }
    .success { color: #17653d; background: #e8f7ed; border: 1px solid #b8e7c8; }
    .error { color: #9f2d2d; background: #fff0f0; border: 1px solid #f1c5c5; }
    .hint { margin: 13px 0 0; color: #718395; font-size: 12px; line-height: 1.7; }
    .pass-card {
      margin-top: 24px; padding: 22px; border: 1px solid #dce8ef;
      border-radius: 18px; background: linear-gradient(145deg, #ffffff, #f3faf9);
    }
    .pass-top { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
    .pass-label { color: #718395; font-size: 12px; font-weight: 700; }
    .pass-number {
      display: inline-flex; align-items: center; justify-content: center;
      min-width: 42px; min-height: 42px; padding: 8px 12px;
      border-radius: 12px; color: #087f70; background: #e6f7f3;
      font-weight: 800; font-size: 18px;
    }
    .pass-name { margin: 22px 0 18px; overflow-wrap: anywhere; font-size: 24px; }
    .status {
      padding: 14px; border-radius: 12px; line-height: 1.7;
      font-size: 14px; font-weight: 700;
    }
    .status.pending { color: #8a5b00; background: #fff7df; border: 1px solid #f1dfaa; }
    .status.approved { color: #17653d; background: #e8f7ed; border: 1px solid #b8e7c8; }
    .status.offline { color: #526779; background: #f1f5f8; border: 1px solid #dce5ec; }
    .secondary-button { color: #087f70; background: #e6f7f3; }
    .secondary-button:hover { color: #fff; background: #06695e; }
    footer { margin-top: 24px; text-align: center; color: #8192a1; font-size: 12px; }
    [hidden] { display: none !important; }
  </style>
</head>
<body>
  <main class="card">
    <span class="badge">● التسجيل مفتوح</span>
    <h1>تسجيل المتسابقين</h1>
    <p class="intro">تابع تسجيلك من هذه الصفحة دون إنشاء حساب.</p>

    <div id="form-section">
      {% if message %}
        <div class="message {{ message_type }}" role="alert">{{ message }}</div>
      {% endif %}
      <form id="registration-form" method="post" action="/" autocomplete="on">
        <input id="device-id" name="device_id" type="hidden">
        <label for="name">اسم المتسابق</label>
        <input id="name" name="name" type="text" maxlength="80"
               placeholder="اكتب الاسم الكامل" required autofocus>
        <button id="submit-button" type="submit">إرسال التسجيل</button>
        <p class="hint">يجب أن يتراوح الاسم بين حرفين و80 حرفًا.</p>
      </form>
    </div>

    <section id="pass-section" class="pass-card" aria-live="polite" hidden>
      <div class="pass-top">
        <span class="pass-label">بطاقة المتسابق الرقمية</span>
        <span class="pass-number">#<span id="pass-number"></span></span>
      </div>
      <h2 id="pass-name" class="pass-name"></h2>
      <div id="pass-status" class="status pending" role="status">
        ⏳ طلبك قيد المراجعة
      </div>
    </section>

    <footer>نظام تسجيل المسابقة</footer>
  </main>

  <script>
    "use strict";
    const STORAGE_KEY = "contestantRegistration";
    const DEVICE_KEY = "contestantDeviceId";
    const initialRegistration = {{ bootstrap_registration | tojson }};
    const formSection = document.getElementById("form-section");
    const registrationForm = document.getElementById("registration-form");
    const deviceIdInput = document.getElementById("device-id");
    const passSection = document.getElementById("pass-section");
    const passName = document.getElementById("pass-name");
    const passNumber = document.getElementById("pass-number");
    const passStatus = document.getElementById("pass-status");
    let activeRegistration = null;
    let statusTimer = null;

    function isValidRegistration(value) {
      return value && typeof value.name === "string" &&
             Number.isInteger(Number(value.number)) && Number(value.number) > 0;
    }

    function readLocalRegistration() {
      try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (!saved) return null;
        const parsed = JSON.parse(saved);
        return isValidRegistration(parsed)
          ? { name: parsed.name, number: Number(parsed.number) }
          : null;
      } catch (error) {
        console.warn("تعذر قراءة بيانات التسجيل المحلية", error);
        return null;
      }
    }

    function saveLocalRegistration(registration) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(registration));
      } catch (error) {
        // تبقى البطاقة ظاهرة لهذه الزيارة حتى لو منع المتصفح التخزين المحلي.
        console.warn("تعذر حفظ بيانات التسجيل في localStorage", error);
      }
    }

    function showPass(registration) {
      activeRegistration = registration;
      formSection.hidden = true;
      passSection.hidden = false;
      passName.textContent = registration.name;
      passNumber.textContent = String(registration.number);
      passStatus.textContent = "⏳ طلبك قيد المراجعة";
      passStatus.className = "status pending";

      refreshStatus();
      if (statusTimer) clearInterval(statusTimer);
      statusTimer = setInterval(refreshStatus, 10000);
    }

    async function refreshStatus() {
      if (!activeRegistration) return;
      try {
        const response = await fetch(
          `/api/status/${encodeURIComponent(activeRegistration.number)}`,
          { method: "GET", cache: "no-store", headers: { "Accept": "application/json" } }
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const result = await response.json();

        if (result.approved === true) {
          passStatus.textContent = "✅ تم نشر اسمك في قناة واتساب. يمكنك العثور عليه بين الأسماء المنشورة مؤخرًا.";
          passStatus.className = "status approved";
        } else {
          passStatus.textContent = "⏳ طلبك قيد المراجعة";
          passStatus.className = "status pending";
        }
      } catch (error) {
        console.warn("تعذر تحديث حالة التسجيل", error);
        passStatus.textContent = "تعذر التحقق من الحالة الآن؛ ستتم إعادة المحاولة تلقائيًا.";
        passStatus.className = "status offline";
      }
    }

    function getOrCreateDeviceId() {
      try {
        let id = localStorage.getItem(DEVICE_KEY);
        if (!id) {
          const bytes = new Uint8Array(16);
          window.crypto.getRandomValues(bytes);
          id = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
          localStorage.setItem(DEVICE_KEY, id);
        }
        return id;
      } catch (error) {
        console.warn("تعذر إنشاء معرّف لهذا المتصفح", error);
        return "";
      }
    }

    if (deviceIdInput) {
      deviceIdInput.value = getOrCreateDeviceId();
    }

    // بعد نجاح POST يرسل الخادم بيانات التسجيل للمتصفح مرة واحدة.
    if (isValidRegistration(initialRegistration)) {
      const current = {
        name: initialRegistration.name,
        number: Number(initialRegistration.number)
      };
      saveLocalRegistration(current);
      showPass(current);
    } else {
      const savedRegistration = readLocalRegistration();
      if (savedRegistration) {
        showPass(savedRegistration);
      }
    }
  </script>
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
        response = requests.post(
            url,
            json=payload,
            timeout=TELEGRAM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f"فشل طلب Telegram API: {method}") from exc

    if not isinstance(result, dict) or not result.get("ok"):
        description = (
            result.get("description", "استجابة غير ناجحة")
            if isinstance(result, dict)
            else "استجابة غير صالحة"
        )
        raise TelegramError(f"Telegram API {method}: {description}")

    return result.get("result")


def build_keyboard(name, contestant_number, reviewed=False):
    """إرجاع زري نسخ الاسم والمراجعة."""
    review_button = {
        "text": "✅ تمت المراجعة" if reviewed else "☑️ تمت المراجعة",
        "callback_data": (
            f"done:{contestant_number}" if reviewed
            else f"review:{contestant_number}"
        ),
    }
    return {
        "inline_keyboard": [
            [{"text": "📋 نسخ الاسم", "copy_text": {"text": name}}],
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
    """قفل عمليات التسجيل وتعديل الملفات عبر الخيوط وعمليات الخادم."""
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
                        app.logger.exception("تعذر تحرير قفل الملفات")
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
    """كتابة ملف مؤقت قبل استبدال الملف الأصلي بصورة ذرية."""
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=str(destination.parent),
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


def read_approved_numbers():
    """قراءة أرقام المتسابقين المعتمدين؛ الملف غير الموجود يعني لا يوجد اعتماد."""
    try:
        content = APPROVED_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()

    return {
        line.strip()
        for line in content.splitlines()
        if line.strip().isdigit()
    }


def read_registered_devices():
    """قراءة بصمة المتصفح ورقم تسجيله من ملف devices.txt."""
    try:
        content = DEVICES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}

    devices = {}
    for line in content.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0] and parts[1].isdigit():
            devices[parts[0]] = parts[1]
    return devices


def mark_number_approved(contestant_number):
    """حفظ رقم المتسابق في approved.txt بشكل آمن ودون تكرار."""
    number_text = str(int(contestant_number))
    temporary_file = None
    with registration_lock():
        approved_numbers = read_approved_numbers()
        if number_text in approved_numbers:
            return

        approved_numbers.add(number_text)
        ordered_numbers = sorted(approved_numbers, key=int)
        content = "".join(f"{number}\n" for number in ordered_numbers)
        temporary_file = stage_text_file(APPROVED_PATH, content)
        try:
            os.replace(temporary_file, APPROVED_PATH)
            temporary_file = None
        finally:
            if temporary_file is not None:
                try:
                    temporary_file.unlink(missing_ok=True)
                except OSError:
                    app.logger.exception("تعذر حذف ملف الاعتماد المؤقت")


def render_page(message=None, message_type=None, status=200, registration=None):
    return (
        render_template_string(
            PAGE,
            message=message,
            message_type=message_type,
            bootstrap_registration=registration,
        ),
        status,
    )


def answer_callback_query(callback_query_id, text=None):
    """إيقاف مؤشر الانتظار الذي يعرضه Telegram بعد ضغط الزر."""
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]
    telegram_api("answerCallbackQuery", payload)


def handle_review_callback(callback_query):
    """حفظ الاعتماد وتحديث رسالة Telegram مع إبقاء زر النسخ."""
    callback_id = callback_query.get("id")
    data = callback_query.get("data", "")
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}

    if not callback_id or not isinstance(data, str):
        return

    if str(chat.get("id", "")) != TELEGRAM_CHAT_ID:
        answer_callback_query(callback_id, "هذه الرسالة ليست في محادثة التسجيل.")
        return

    if TELEGRAM_ADMIN_USER_ID:
        clicker_id = str((callback_query.get("from") or {}).get("id", ""))
        if clicker_id != TELEGRAM_ADMIN_USER_ID:
            answer_callback_query(callback_id, "هذا الزر مخصص للمسؤول فقط.")
            return

    match = re.fullmatch(r"(review|done):(\d+)", data)
    if not match:
        answer_callback_query(callback_id, "زر غير معروف.")
        return

    action, number_text = match.groups()
    message_text = message.get("text", "")

    if action == "done" or message_text.startswith(REVIEWED_PREFIX):
        # جعل العملية idempotent، ومعالجة حالة ملف اعتماد قديم/مفقود.
        mark_number_approved(number_text)
        answer_callback_query(callback_id, "تم اعتماد هذا التسجيل مسبقًا.")
        return

    registration_match = re.fullmatch(
        r"(\d+) - (.+)",
        message_text,
        flags=re.DOTALL,
    )
    if not registration_match or registration_match.group(1) != number_text:
        answer_callback_query(callback_id, "تعذر التحقق من بيانات التسجيل.")
        return

    contestant_number = int(number_text)
    name = registration_match.group(2)

    try:
        answer_callback_query(callback_id, "تم اعتماد التسجيل.")
    except TelegramError:
        app.logger.exception("تعذر إظهار إشعار callback في تيليجرام")

    # يعتمد التسجيل في ملف الحالة. هذا لا ينشر الاسم تلقائيًا في قناة عامة.
    mark_number_approved(contestant_number)

    edited_text = f"{REVIEWED_PREFIX}{message_text}"
    telegram_api(
        "editMessageText",
        {
            "chat_id": chat["id"],
            "message_id": message["message_id"],
            "text": edited_text,
            "reply_markup": build_keyboard(
                name,
                contestant_number,
                reviewed=True,
            ),
        },
    )


@app.route("/", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_page()

    # إزالة المسافات الزائدة والمتكررة؛ الأقواس مكتملة.
    name = re.sub(r"\s+", " ", request.form.get("name", "").strip())
    device_id = request.form.get("device_id", "").strip()
    if not 2 <= len(name) <= MAX_NAME_LENGTH:
        return render_page(
            "يرجى إدخال اسم يتراوح بين حرفين و80 حرفًا.",
            "error",
            400,
        )
    if not re.fullmatch(r"[0-9a-fA-F-]{32,36}", device_id):
        return render_page(
            "تعذر تحديد هذا المتصفح. فعّل التخزين المحلي ثم أعد تحميل الصفحة.",
            "error",
            400,
        )

    device_hash = hashlib.sha256(device_id.encode("utf-8")).hexdigest()

    staged_names = None
    staged_counter = None
    staged_devices = None

    try:
        with registration_lock():
            registered_devices = read_registered_devices()
            if device_hash in registered_devices:
                return render_page(
                    "سبق تسجيل متسابق من هذا المتصفح. لا يمكن تسجيل اسم آخر منه.",
                    "error",
                    409,
                )

            registered_names = read_registered_names()
            existing_normalized = {
                registered.casefold() for registered in registered_names
            }

            if name.casefold() in existing_normalized:
                return render_page(
                    "عذراً، هذا الاسم مسجل مسبقاً، يرجى إدخال اسم آخر.",
                    "error",
                    409,
                )

            contestant_number = read_counter(len(registered_names)) + 1
            updated_names = registered_names + [name]
            names_content = "".join(
                f"{registered}\n" for registered in updated_names
            )
            counter_content = f"{contestant_number}\n"
            registered_devices[device_hash] = str(contestant_number)
            devices_content = "".join(
                f"{key}\t{registered_devices[key]}\n"
                for key in sorted(registered_devices)
            )

            staged_names = stage_text_file(NAMES_PATH, names_content)
            staged_counter = stage_text_file(COUNTER_PATH, counter_content)
            staged_devices = stage_text_file(DEVICES_PATH, devices_content)

            # حفظ التسجيل محليًا بعد تأكيد إرسال رسالة تيليجرام بنجاح.
            send_registration_message(name, contestant_number)
            os.replace(staged_names, NAMES_PATH)
            staged_names = None
            os.replace(staged_counter, COUNTER_PATH)
            staged_counter = None
            os.replace(staged_devices, DEVICES_PATH)
            staged_devices = None

        return render_page(
            status=200,
            registration={"name": name, "number": contestant_number},
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
            "حدث خطأ أثناء حفظ التسجيل. يرجى المحاولة لاحقًا.",
            "error",
            500,
        )
    except Exception:
        app.logger.exception("خطأ غير متوقع أثناء التسجيل")
        return render_page(
            "حدث خطأ غير متوقع. يرجى المحاولة لاحقًا.",
            "error",
            500,
        )
    finally:
        for staged_file in (staged_names, staged_counter, staged_devices):
            if staged_file is not None:
                try:
                    staged_file.unlink(missing_ok=True)
                except OSError:
                    app.logger.exception("تعذر حذف ملف مؤقت للتسجيل")


@app.route("/api/status/<int:contestant_number>", methods=["GET"])
def api_registration_status(contestant_number):
    """إرجاع حالة اعتماد متسابق حسب رقمه التسلسلي."""
    if contestant_number < 1:
        return jsonify(approved=False, error="invalid_number"), 400

    try:
        with registration_lock():
            is_approved = str(contestant_number) in read_approved_numbers()
    except (OSError, UnicodeError):
        app.logger.exception("تعذر قراءة ملف approved.txt")
        return jsonify(approved=False, error="status_unavailable"), 503

    response = jsonify(approved=is_approved)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response, 200


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    """استقبال ضغط زر «تمت المراجعة» من Telegram عبر Webhook."""
    if not TELEGRAM_WEBHOOK_SECRET:
        app.logger.error("TELEGRAM_WEBHOOK_SECRET غير مضبوط")
        return jsonify(ok=False, error="webhook_not_configured"), 503

    supplied_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token",
        "",
    )
    if not hmac.compare_digest(supplied_secret, TELEGRAM_WEBHOOK_SECRET):
        return jsonify(ok=False, error="unauthorized"), 403

    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return jsonify(ok=False, error="invalid_update"), 400

    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        return jsonify(ok=True), 200

    try:
        handle_review_callback(callback_query)
    except TelegramError:
        app.logger.exception("فشل تنفيذ Telegram API أثناء معالجة callback")
        return jsonify(ok=False, error="telegram_api_error"), 500
    except (OSError, UnicodeError, RegistrationError):
        app.logger.exception("تعذر حفظ حالة الاعتماد")
        return jsonify(ok=False, error="approval_storage_error"), 500
    except Exception:
        app.logger.exception("فشل معالجة Telegram callback")
        return jsonify(ok=False, error="callback_processing_error"), 500

    return jsonify(ok=True), 200


if __name__ == "__main__":
    # للتشغيل المحلي فقط. على Render استخدم: gunicorn app:app
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
    )
