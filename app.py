    name = clean_name(
        request.form.get(
            "name",
            ""
        )
    )

    # التحقق من طول الاسم
    if len(name) < 2 or len(name) > 80:
        return render_template_string(
            PAGE,
            success=False,
            error="يرجى إدخال اسم يتراوح بين حرفين و80 حرفًا."
        ), 400

    # التحقق هل الاسم مكرر مسبقاً
    if name_exists(name):
        return render_template_string(
            PAGE,
            success=False,
            error="عذراً، هذا الاسم مسجل مسبقاً، يرجى إدخال اسم آخر."
        ), 409

    try:
        number = get_next_number()
        send_to_telegram(number, name)
        save_name(name)

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
    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get("PORT", "5000")
        ),
        debug=False
    )
