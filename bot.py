"""Entry point for Wolvesville clan bot."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import requests
from aiogram import types
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.filters import Command

from bot_app import create_app_context
from bot_app.scheduler import setup_scheduler
from config import (
    ADMIN_IDS,
    ADMIN_NOTIFICATION_CHANNEL,
    AUTHORIZED_GROUPS,
    CLAN_ID,
    OWNER_CHAT_ID,
    SKIP_IMAGE_PATH,
    TOKEN,
    WOLVESVILLE_API_KEY,
)
from handlers import register_user_flow_handlers
from services.identity_service import IdentityService
from services.maintenance_service import MaintenanceService
from services.mission_service import MissionService
from services.notification_service import (
    EnhancedNotificationService,
    NotificationType,
)

try:
    from middleware.auth_middleware import GroupAuthorizationMiddleware

    MIDDLEWARE_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - middleware opzionale
    print(f"⚠️ GroupAuthorizationMiddleware non disponibile: {exc}")
    GroupAuthorizationMiddleware = None  # type: ignore
    MIDDLEWARE_AVAILABLE = False

bot_logger = None
try:
    from utils.logger import bot_logger as imported_bot_logger

    if imported_bot_logger is not None:
        bot_logger = imported_bot_logger
        print("✅ bot_logger importato dalla utils.logger")
    else:
        print("⚠️ bot_logger è None dopo import")
except ImportError as exc:  # pragma: no cover - solo log
    print(f"❌ ImportError bot_logger: {exc}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

LOG_PUBLIC_IP = os.getenv("LOG_PUBLIC_IP", "false").lower() in {"1", "true", "yes", "on"}
PROFILE_AUTO_SYNC_INTERVAL_MINUTES = int(
    os.getenv("PROFILE_AUTO_SYNC_INTERVAL_MINUTES", "15")
)

notification_service: Optional[EnhancedNotificationService] = None
identity_service: Optional[IdentityService] = None
maintenance_service: Optional[MaintenanceService] = None
mission_service: Optional[MissionService] = None

def maybe_log_public_ip() -> None:
    """Recupera e registra l'IP pubblico solo quando esplicitamente richiesto."""

    if not LOG_PUBLIC_IP:
        return

    try:
        response = requests.get("https://ifconfig.me/ip", timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - log difensivo
        logger.warning("Impossibile recuperare l'IP pubblico: %s", exc)
        return

    public_ip = response.text.strip()
    if public_ip:
        logger.info("IP pubblico del bot: %s", public_ip)

def schedule_admin_notification(
    message: str,
    *,
    notification_type: NotificationType = NotificationType.INFO,
    urgent: bool = False,
) -> None:
    """Invia una notifica agli admin senza bloccare il flusso principale."""

    if not notification_service:
        return

    async def _send() -> None:
        try:
            assert notification_service is not None
            await notification_service.send_admin_notification(
                message,
                notification_type=notification_type,
                urgent=urgent,
            )
        except Exception as exc:  # pragma: no cover - solo logging
            logger.warning("Notifica admin fallita: %s", exc)

    try:
        asyncio.create_task(_send())
    except RuntimeError:  # pragma: no cover - loop non attivo
        logger.warning(
            "Loop asyncio non attivo: impossibile pianificare la notifica admin immediatamente."
        )


class LoggingMiddleware(BaseMiddleware):
    """Registra i messaggi in ingresso e sincronizza i profili Telegram."""

    async def __call__(self, handler, event, data):
        if isinstance(event, types.Message):
            chat_id = event.chat.id
            thread_id = event.message_thread_id if event.is_topic_message else None
            user_id = event.from_user.id
            text = event.text if event.text else "<Non testuale>"
            logger.info(
                "Messaggio ricevuto | Chat ID: %s | Thread ID: %s | Utente ID: %s | Testo: %s",
                chat_id,
                thread_id,
                user_id,
                text,
            )
            try:
                if identity_service is not None:
                    await identity_service.ensure_telegram_profile_synced(event.from_user)
            except Exception as exc:  # pragma: no cover - evitare crash middleware
                logger.warning("Sync profilo Telegram fallita per %s: %s", user_id, exc)
        return await handler(event, data)


async def manual_cleanup(message: types.Message) -> None:
    """Comando manuale per admin per pulire duplicati e controllare uscite clan."""

    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Non hai i permessi per questo comando.")
        return

    if maintenance_service is None:
        await message.answer("❌ Servizio di manutenzione non disponibile.")
        return

    try:
        loading_msg = await message.answer("🔄 Avvio pulizia database...")

        await maintenance_service.clean_duplicate_users()
        await maintenance_service.check_clan_departures()

        await loading_msg.edit_text(
            "✅ Pulizia completata!\n\n🗂️ Duplicati rimossi\n👥 Controllo uscite clan eseguito"
        )
    except Exception as exc:  # pragma: no cover - solo log
        logger.error("Errore cleanup manuale: %s", exc)
        await message.answer("❌ Errore durante la pulizia. Controlla i log.")


def configure_middlewares(dispatcher) -> None:
    """Registra i middleware applicativi sul dispatcher."""

    if bot_logger is not None:
        bot_logger.add_telegram_handler(bot, ADMIN_IDS)

    auth_middleware = None
    if MIDDLEWARE_AVAILABLE and GroupAuthorizationMiddleware is not None:
        auth_middleware = GroupAuthorizationMiddleware(
            authorized_groups=set(AUTHORIZED_GROUPS),
            admin_ids=ADMIN_IDS,
            notification_service=notification_service,
        )

    dispatcher.update.middleware(LoggingMiddleware())
    if auth_middleware is not None:
        dispatcher.update.middleware(auth_middleware)


app_context = create_app_context(
    token=TOKEN,
    mongo_uri="mongodb+srv://Admin:X3TaVDKSSQDcfUG@wolvesville.6mrnmcn.mongodb.net/?retryWrites=true&w=majority&appName=Wolvesville",
    database_name="Wolvesville",
    admin_ids=ADMIN_IDS,
    admin_channel_id=ADMIN_NOTIFICATION_CHANNEL,
    owner_id=OWNER_CHAT_ID,
)

bot = app_context.bot
dp = app_context.dispatcher
notification_service = app_context.notification_service
db_manager = app_context.db_manager
scheduler = app_context.scheduler

identity_service = IdentityService(
    bot=bot,
    db_manager=db_manager,
    wolvesville_api_key=WOLVESVILLE_API_KEY,
    schedule_admin_notification=schedule_admin_notification,
    logger=logger,
)

maintenance_service = MaintenanceService(
    bot=bot,
    db_manager=db_manager,
    identity_service=identity_service,
    clan_id=CLAN_ID,
    wolvesville_api_key=WOLVESVILLE_API_KEY,
    admin_ids=ADMIN_IDS,
    logger=logger,
)

mission_service = MissionService(
    bot=bot,
    db_manager=db_manager,
    identity_service=identity_service,
    maintenance_service=maintenance_service,
    clan_id=CLAN_ID,
    wolvesville_api_key=WOLVESVILLE_API_KEY,
    skip_image_path=SKIP_IMAGE_PATH,
    logger=logger,
    schedule_admin_notification=schedule_admin_notification,
)

configure_middlewares(dp)
register_user_flow_handlers(
    dp,
    bot=bot,
    logger=logger,
    mission_service=mission_service,
    db_manager=db_manager,
    identity_service=identity_service,
    notification_service=notification_service,
    wolvesville_api_key=WOLVESVILLE_API_KEY,
    clan_id=CLAN_ID,
    skip_image_path=SKIP_IMAGE_PATH,
    admin_ids=ADMIN_IDS,
    authorized_groups=AUTHORIZED_GROUPS,
    schedule_admin_notification=schedule_admin_notification,
)
mission_service.register_handlers(dp)
dp.message.register(manual_cleanup, Command("cleanup"))


async def main() -> None:
    maybe_log_public_ip()
    setup_scheduler(
        scheduler,
        maintenance_service=maintenance_service,
        mission_service=mission_service,
        identity_service=identity_service,
        profile_auto_sync_minutes=PROFILE_AUTO_SYNC_INTERVAL_MINUTES,
        logger=logger,
    )
    await maintenance_service.prepopulate_users()
    await identity_service.refresh_linked_profiles()

    try:
        await notification_service.send_bot_status_update(
            "AVVIATO",
            "Bot inizializzato correttamente con sistemi di sicurezza attivi."
            f" Gruppi autorizzati: {len(AUTHORIZED_GROUPS)}",
        )
    except Exception as exc:  # pragma: no cover - solo log
        if bot_logger is not None:
            bot_logger.log_error(exc, "Errore invio notifica avvio bot")
        else:
            logger.error("Errore invio notifica avvio bot: %s", exc)

    logger.info("Avvio del bot.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
