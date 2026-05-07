import os
import sys
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
from dotenv import load_dotenv
from ads_utils import upload_to_meta, upload_to_snapchat, upload_to_tiktok
from drive_utils import (
    download_file_from_drive,
    download_file_by_name,
    get_file_id_from_link,
    get_folder_id_from_link,
    list_subfolders,
    list_files_in_folder,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── متغيرات البيئة ───────────────────────────────────────────────────────────
# إلزامية — البوت لا يعمل بدونها
_REQUIRED_ENV = [
    "TELEGRAM_BOT_TOKEN",
    "GOOGLE_DRIVE_CLIENT_ID",
    "GOOGLE_DRIVE_CLIENT_SECRET",
    "GOOGLE_DRIVE_REFRESH_TOKEN",
]

# اختيارية — لكل منصة
_PLATFORM_ENV = {
    "meta": ["META_ADS_ACCESS_TOKEN", "META_ADS_ACCOUNT_ID"],
    "snapchat": ["SNAPCHAT_ADS_CLIENT_ID", "SNAPCHAT_ADS_CLIENT_SECRET",
                 "SNAPCHAT_ADS_REFRESH_TOKEN", "SNAPCHAT_ADS_AD_ACCOUNT_ID"],
    "tiktok": ["TIKTOK_ADS_ACCESS_TOKEN", "TIKTOK_ADS_ADVERTISER_ID"],
}


def _validate_env() -> None:
    """تتحقق من المتغيرات الإلزامية وتحدد المنصات المتاحة."""
    missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
    if missing:
        logger.critical("❌ متغيرات بيئة إلزامية ناقصة: %s", ", ".join(missing))
        sys.exit(1)

    available = []
    unavailable = []
    for platform, keys in _PLATFORM_ENV.items():
        if all(os.getenv(k) for k in keys):
            available.append(platform)
        else:
            unavailable.append(platform)

    if available:
        logger.info("✅ منصات جاهزة: %s", ", ".join(available))
    if unavailable:
        logger.warning("⚠️ منصات غير مفعّلة (متغيرات ناقصة): %s", ", ".join(unavailable))

SELECT_PLATFORM, GET_DRIVE_LINK, CONFIRM_UPLOAD, GET_MULTI_LINKS, GET_FOLDER_LINK, SELECT_SUBFOLDER = range(6)

PLATFORM_NAMES = {
    "meta": "Meta Ads 📘",
    "snapchat": "Snapchat Ads 👻",
    "tiktok": "TikTok Ads 🎵",
}

def _platform_is_configured(platform: str) -> bool:
    keys = _PLATFORM_ENV.get((platform or "").strip().lower(), [])
    return bool(keys) and all((os.getenv(k) or "").strip() for k in keys)


def _build_platform_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for p in ["meta", "snapchat", "tiktok"]:
        if _platform_is_configured(p):
            rows.append([InlineKeyboardButton(PLATFORM_NAMES[p], callback_data=p)])
    # لو مفيش أي منصة مهيأة، اعرض Meta كخيار (للتوضيح) لكن هنمنع الرفع برسالة واضحة لاحقاً.
    if not rows:
        rows = [[InlineKeyboardButton(PLATFORM_NAMES["meta"], callback_data="meta")]]
    return InlineKeyboardMarkup(rows)


def _detect_file_type(mime_type: str, path: str = "") -> str:
    if mime_type:
        if mime_type.startswith("video/"):
            return "video"
        if mime_type.startswith("image/"):
            return "image"
    ext = os.path.splitext(path)[1].lower()
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp", ".wmv", ".flv", ".ts", ".mts"}
    return "video" if ext in video_exts else "image"


async def _upload_file(local_path: str, platform: str, mime_type: str = "") -> dict:
    file_type = _detect_file_type(mime_type, local_path)
    logger.info(f"Uploading '{os.path.basename(local_path)}' as {file_type} to {platform}")
    if platform == "meta":
        return await asyncio.to_thread(upload_to_meta, local_path, file_type)
    elif platform == "snapchat":
        return await asyncio.to_thread(upload_to_snapchat, local_path, file_type)
    elif platform == "tiktok":
        return await asyncio.to_thread(upload_to_tiktok, local_path, file_type)
    return {}


async def start(update: Update, context) -> int:
    await update.message.reply_text(
        "مرحباً! 👋 أنا بوت أتمتة الإعلانات.\n\n"
        "/upload — رفع ملف واحد\n"
        "/upload_multi — رفع روابط متعددة\n"
        "/upload_folder — رفع فولدر كامل\n"
        "/cancel — إلغاء"
    )
    return ConversationHandler.END


async def select_platform(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    platform = query.data
    context.user_data["platform"] = platform
    name = PLATFORM_NAMES.get(platform, platform)
    mode = context.user_data.get("mode", "single")

    if not _platform_is_configured(platform):
        need = ", ".join(_PLATFORM_ENV.get(platform, []))
        await query.edit_message_text(
            f"❌ {name} غير مهيأ حالياً.\n"
            f"{('🔑 المطلوب في .env: ' + need + '\\n') if need else ''}"
            f"جهّز التوكنات ثم جرّب تاني."
        )
        return ConversationHandler.END

    if mode == "folder":
        await query.edit_message_text(f"✅ {name}\n\nأرسل رابط فولدر Google Drive:")
        return GET_FOLDER_LINK
    elif mode == "multi":
        await query.edit_message_text(f"✅ {name}\n\nأرسل الروابط (كل رابط في سطر):")
        return GET_MULTI_LINKS
    else:
        await query.edit_message_text(f"✅ {name}\n\nأرسل رابط Google Drive للملف:")
        return GET_DRIVE_LINK


async def upload_command(update: Update, context) -> int:
    context.user_data["mode"] = "single"
    await update.message.reply_text("اختر المنصة:", reply_markup=_build_platform_keyboard())
    return SELECT_PLATFORM


async def get_drive_link(update: Update, context) -> int:
    link = update.message.text.strip()
    file_id = get_file_id_from_link(link)
    if not file_id:
        await update.message.reply_text("❌ رابط غير صالح. حاول مرة أخرى:")
        return GET_DRIVE_LINK
    context.user_data["file_id"] = file_id
    name = PLATFORM_NAMES.get(context.user_data["platform"], "")
    await update.message.reply_text(f"📋 المنصة: {name}\nالرابط: {link}\n\nهل تريد المتابعة؟ (نعم / لا)")
    return CONFIRM_UPLOAD


async def confirm_upload(update: Update, context) -> int:
    if update.message.text.strip().lower() not in ["نعم", "yes", "y"]:
        await update.message.reply_text("❌ تم الإلغاء.")
        return ConversationHandler.END
    await update.message.reply_text("⏳ جارٍ التحميل من Google Drive...")
    file_id = context.user_data["file_id"]
    platform = context.user_data["platform"]
    base_path = f"/tmp/temp_{file_id}"
    local_path = base_path
    try:
        local_path, mime_type = await asyncio.to_thread(download_file_from_drive, file_id, base_path)
        await update.message.reply_text("✅ تم التحميل. جارٍ الرفع...")
        result = await _upload_file(local_path, platform, mime_type)
        await update.message.reply_text("🎉 تمت العملية بنجاح!")
    except Exception as e:
        logger.error(f"confirm_upload error: {e}")
        await update.message.reply_text(f"❌ خطأ: {e}")
    finally:
        for path in {base_path, local_path}:
            if os.path.exists(path):
                os.remove(path)
    return ConversationHandler.END


async def upload_multi_command(update: Update, context) -> int:
    context.user_data["mode"] = "multi"
    await update.message.reply_text("اختر المنصة:", reply_markup=_build_platform_keyboard())
    return SELECT_PLATFORM


async def get_multi_links(update: Update, context) -> int:
    links = [l.strip() for l in update.message.text.strip().splitlines() if l.strip()]
    platform = context.user_data["platform"]
    if not links:
        await update.message.reply_text("❌ لم يتم إرسال روابط:")
        return GET_MULTI_LINKS
    await update.message.reply_text(f"⏳ جارٍ معالجة {len(links)} ملف(ات)...")
    ok = fail = 0
    for link in links:
        file_id = get_file_id_from_link(link)
        if not file_id:
            fail += 1
            await update.message.reply_text(f"⚠️ رابط غير صالح: {link}")
            continue
        base_path = f"/tmp/temp_{file_id}"
        local_path = base_path
        try:
            local_path, mime_type = await asyncio.to_thread(download_file_from_drive, file_id, base_path)
            await _upload_file(local_path, platform, mime_type)
            ok += 1
        except Exception as e:
            fail += 1
            await update.message.reply_text(f"❌ فشل: {link}\n{e}")
        finally:
            for path in {base_path, local_path}:
                if os.path.exists(path):
                    os.remove(path)
    await update.message.reply_text(f"✅ انتهى!\n• نجح: {ok}\n• فشل: {fail}")
    return ConversationHandler.END


async def upload_folder_command(update: Update, context) -> int:
    context.user_data["mode"] = "folder"
    await update.message.reply_text("اختر المنصة:", reply_markup=_build_platform_keyboard())
    return SELECT_PLATFORM


async def get_folder_link(update: Update, context) -> int:
    link = update.message.text.strip()
    folder_id = get_folder_id_from_link(link)
    if not folder_id:
        await update.message.reply_text("❌ رابط غير صالح:")
        return GET_FOLDER_LINK
    context.user_data["folder_id"] = folder_id
    await update.message.reply_text("⏳ جارٍ تحميل قائمة الفولدرات...")
    try:
        subfolders = await asyncio.to_thread(list_subfolders, folder_id)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")
        return ConversationHandler.END
    context.user_data["subfolders"] = subfolders
    rows = [[InlineKeyboardButton(f"📁 {f['name']}", callback_data=f"sf:{i}")] for i, f in enumerate(subfolders[:48])]
    rows.append([InlineKeyboardButton("📂 رفع كل الملفات مباشرة", callback_data="sf:all")])
    msg = f"وجدت {len(subfolders)} فولدر فرعي. اختر:" if subfolders else "لا توجد فولدرات فرعية:"
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(rows))
    return SELECT_SUBFOLDER


async def select_subfolder(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    root_folder_id = context.user_data["folder_id"]
    platform = context.user_data["platform"]
    subfolders = context.user_data.get("subfolders", [])
    if data == "sf:all":
        target_id, folder_name = root_folder_id, "الفولدر الرئيسي"
    else:
        idx = int(data.split(":")[1])
        target_id = subfolders[idx]["id"]
        folder_name = subfolders[idx]["name"]
    await query.edit_message_text(f"⏳ جارٍ جلب ملفات: {folder_name}...")
    try:
        files = await asyncio.to_thread(list_files_in_folder, target_id)
    except Exception as e:
        await query.message.reply_text(f"❌ خطأ: {e}")
        return ConversationHandler.END
    if not files:
        await query.message.reply_text("❌ لا توجد ملفات.")
        return ConversationHandler.END
    images = [f for f in files if f.get("mimeType", "").startswith("image/")]
    videos = [f for f in files if f.get("mimeType", "").startswith("video/")]
    await query.message.reply_text(
        f"📊 «{folder_name}»\n• صور: {len(images)} | فيديوهات: {len(videos)}\n⏳ جارٍ الرفع..."
    )
    ok_img = ok_vid = fail = 0
    for file_info in files:
        file_id = file_info["id"]
        file_name = file_info["name"]
        mime_type = file_info.get("mimeType", "")
        local_path = f"/tmp/{file_name}"
        try:
            await asyncio.to_thread(download_file_by_name, file_id, file_name, "/tmp")
            await _upload_file(local_path, platform, mime_type)
            if mime_type.startswith("video/"):
                ok_vid += 1
            else:
                ok_img += 1
        except Exception as e:
            fail += 1
            await query.message.reply_text(f"⚠️ فشل: {file_name}\n{e}")
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)
    await query.message.reply_text(
        f"✅ انتهى «{folder_name}»\n• صور: {ok_img}\n• فيديوهات: {ok_vid}\n• فشل: {fail}"
    )
    return ConversationHandler.END


async def cancel(update: Update, context) -> int:
    await update.message.reply_text("❌ تم الإلغاء.")
    return ConversationHandler.END


def main() -> None:
    _validate_env()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()

    shared_states = {
        SELECT_PLATFORM: [CallbackQueryHandler(select_platform)],
    }

    single_conv = ConversationHandler(
        entry_points=[CommandHandler("upload", upload_command)],
        states={
            **shared_states,
            GET_DRIVE_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_drive_link)],
            CONFIRM_UPLOAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_upload)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    multi_conv = ConversationHandler(
        entry_points=[CommandHandler("upload_multi", upload_multi_command)],
        states={
            **shared_states,
            GET_MULTI_LINKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_multi_links)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    folder_conv = ConversationHandler(
        entry_points=[CommandHandler("upload_folder", upload_folder_command)],
        states={
            **shared_states,
            GET_FOLDER_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_folder_link)],
            SELECT_SUBFOLDER: [CallbackQueryHandler(select_subfolder, pattern="^sf:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(single_conv)
    app.add_handler(multi_conv)
    app.add_handler(folder_conv)

    # ── Webhook (Railway) أو Polling (local) ────────────────────────────────
    port = int(os.getenv("PORT", "8080"))
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    webhook_url = os.getenv("WEBHOOK_URL", "")
    public_url = webhook_url or (f"https://{railway_domain}" if railway_domain else "")

    if public_url:
        logger.info("🚀 Webhook mode — %s", public_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=token,
            webhook_url=f"{public_url}/{token}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("🔄 Polling mode (no public URL configured)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
