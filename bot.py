"""Entry point for the Wolvesville clan bot."""

from __future__ import annotations

import asyncio

import logging
import os
from typing import Optional

import requests
from aiogram import types
from aiogram.filters import ChatMemberUpdatedFilter, Command, KICKED, MEMBER
from aiogram.types import ChatMemberUpdated, Message

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

try:  # pragma: no cover - import difensivo
    from middleware import GroupAuthorizationMiddleware, LoggingMiddleware

    MIDDLEWARE_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - il progetto può funzionare senza middleware custom
    print(f"⚠️ Impossibile importare i middleware personalizzati: {exc}")
    GroupAuthorizationMiddleware = None  # type: ignore
    LoggingMiddleware = None  # type: ignore
    MIDDLEWARE_AVAILABLE = False

from reward_service import RewardService
from statistics_service import StatisticsService
from services.identity_service import IdentityService
from services.maintenance_service import MaintenanceService
from services.mission_service import MissionService
from services.notification_service import (
    EnhancedNotificationService,
    NotificationType,
)
from utils.logger import bot_logger


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

LOG_PUBLIC_IP = os.getenv("LOG_PUBLIC_IP", "false").lower() in {"1", "true", "yes", "on"}
PROFILE_AUTO_SYNC_INTERVAL_MINUTES = int(
    os.getenv("PROFILE_AUTO_SYNC_INTERVAL_MINUTES", "15")
)

MONGO_URI = (
    "mongodb+srv://Admin:X3TaVDKSSQDcfUG@wolvesville.6mrnmcn.mongodb.net/"
    "?retryWrites=true&w=majority&appName=Wolvesville"
)
DATABASE_NAME = "Wolvesville"

notification_service: Optional[EnhancedNotificationService] = None
identity_service: Optional[IdentityService] = None
maintenance_service: Optional[MaintenanceService] = None
mission_service: Optional[MissionService] = None
reward_service: Optional[RewardService] = None
statistics_service: Optional[StatisticsService] = None


# ---------------------------------------------------------------------------
# Helper per logging e notifiche
# ---------------------------------------------------------------------------
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
        assert notification_service is not None
        try:
            await notification_service.send_admin_notification(
                message,
                notification_type=notification_type,
                urgent=urgent,
            )
        except Exception as exc:  # pragma: no cover - logging difensivo
            logger.warning("Invio notifica admin fallito: %s", exc)

    try:
        asyncio.create_task(_send())
    except RuntimeError:  # pragma: no cover - loop non attivo
        logger.warning(
            "Loop asyncio non attivo: impossibile pianificare la notifica admin immediatamente."
        )


# ---------------------------------------------------------------------------
# Inizializzazione del contesto applicativo e dei servizi
# ---------------------------------------------------------------------------
app_context = create_app_context(
    token=TOKEN,
    mongo_uri=MONGO_URI,
    database_name=DATABASE_NAME,
    admin_ids=ADMIN_IDS,
    admin_channel_id=ADMIN_NOTIFICATION_CHANNEL,
    owner_id=OWNER_CHAT_ID,
)

bot = app_context.bot
dp = app_context.dispatcher
notification_service = app_context.notification_service
db_manager = app_context.db_manager
scheduler = app_context.scheduler
rewards_repository = app_context.rewards_repository

identity_service = IdentityService(
    bot=bot,
    db_manager=db_manager,
    wolvesville_api_key=WOLVESVILLE_API_KEY,
    schedule_admin_notification=schedule_admin_notification,
    logger=logger,
)

reward_service = RewardService(
    repository=rewards_repository,
    notification_service=notification_service,
    logger=logger,
)

maintenance_service = MaintenanceService(
    bot=bot,
    db_manager=db_manager,
    identity_service=identity_service,
    clan_id=CLAN_ID,
    wolvesville_api_key=WOLVESVILLE_API_KEY,
    admin_ids=ADMIN_IDS,
    reward_service=reward_service,
    logger=logger,
)

mission_service = MissionService(
    bot=bot,
    db_manager=db_manager,
    identity_service=identity_service,
    maintenance_service=maintenance_service,
    wolvesville_api_key=WOLVESVILLE_API_KEY,
    clan_id=CLAN_ID,
    logger=logger,
    reward_service=reward_service,
)

statistics_service = StatisticsService(
    db_manager=db_manager,
    notification_service=notification_service,
    logger=logger,
)


# ---------------------------------------------------------------------------
# Configurazione middleware e registrazione handler
# ---------------------------------------------------------------------------
def configure_middlewares() -> None:
    """Registra i middleware applicativi sul dispatcher."""

    if LoggingMiddleware is not None:
        dp.update.middleware(LoggingMiddleware())

    if bot_logger is not None:
        bot_logger.add_telegram_handler(bot, ADMIN_IDS)

    if (
        MIDDLEWARE_AVAILABLE
        and GroupAuthorizationMiddleware is not None
        and notification_service is not None
    ):
        dp.update.middleware(
            GroupAuthorizationMiddleware(
                authorized_groups=set(AUTHORIZED_GROUPS),
                admin_ids=ADMIN_IDS,
                notification_service=notification_service,
            )
        )


configure_middlewares()

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
    reward_service=reward_service,
)

mission_service.register_handlers(dp)


# ---------------------------------------------------------------------------
# Comandi e handler specifici del bot
# ---------------------------------------------------------------------------
async def manual_cleanup(message: Message) -> None:
    """Consente agli admin di avviare manualmente le operazioni di pulizia."""

    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Non hai i permessi per questo comando.")
        return

    if maintenance_service is None:
        await message.answer("❌ Servizio di manutenzione non disponibile.")
        return

    status_message = await message.answer("🔄 Avvio pulizia database...")

    try:
        await maintenance_service.clean_duplicate_users()
        await maintenance_service.check_clan_departures()
    except Exception as exc:  # pragma: no cover - solo log
        logger.error("Errore durante la pulizia manuale: %s", exc)
        await status_message.edit_text("❌ Errore durante la pulizia. Controlla i log.")
        return

    await status_message.edit_text(
        "✅ Pulizia completata!\n\n🗂️ Duplicati rimossi\n👥 Controllo uscite clan eseguito"
    )


dp.message.register(manual_cleanup, Command("cleanup"))


async def handle_bot_removed(event: ChatMemberUpdated) -> None:
    """Notifica gli admin quando il bot viene rimosso da un gruppo autorizzato."""

    chat_id = event.chat.id
    chat_title = event.chat.title or "Chat"

    logger.info("Bot rimosso dalla chat: %s (%s)", chat_id, chat_title)

    if chat_id in AUTHORIZED_GROUPS:
        message = (
            "⚠️ **BOT RIMOSSO DA GRUPPO AUTORIZZATO**\n\n"
            f"👥 **Gruppo:** {chat_title}\n"
            f"🆔 **Chat ID:** `{chat_id}`\n\n"
            "🔁 **Azione:** Verificare se l'uscita è intenzionale"
        )
        schedule_admin_notification(
            message,
            notification_type=NotificationType.WARNING,
            urgent=True,
        )


dp.my_chat_member.register(
    handle_bot_removed,
    ChatMemberUpdatedFilter(member_status_changed=KICKED),
)


async def handle_bot_added(event: ChatMemberUpdated) -> None:
    """Gestisce l'aggiunta del bot ad una chat, applicando i controlli di sicurezza."""

    chat_id = event.chat.id
    chat_title = event.chat.title or "Chat"
    user_id = event.from_user.id if event.from_user else None

    logger.info("Bot aggiunto alla chat: %s (%s)", chat_id, chat_title)

    if chat_id not in AUTHORIZED_GROUPS:
        logger.warning("Bot aggiunto a gruppo non autorizzato: %s (%s)", chat_title, chat_id)

        if not (
            MIDDLEWARE_AVAILABLE and GroupAuthorizationMiddleware is not None
        ) and notification_service is not None:
            await notification_service.handle_unauthorized_group_join(
                chat_id=chat_id,
                chat_title=chat_title,
                user_id=user_id,
            )

        if not (
            MIDDLEWARE_AVAILABLE and GroupAuthorizationMiddleware is not None
        ):
            try:
                await bot.leave_chat(chat_id)
                logger.info("Uscito dal gruppo non autorizzato: %s", chat_id)
            except Exception as exc:  # pragma: no cover - logging difensivo
                logger.error(
                    "Errore durante l'uscita dal gruppo non autorizzato %s: %s",
                    chat_id,
                    exc,
                )
        return

    if notification_service is not None:
        await notification_service.send_authorized_group_notification(
            chat_id, chat_title
        )

    logger.info("Bot aggiunto con successo al gruppo autorizzato: %s", chat_id)


dp.my_chat_member.register(
    handle_bot_added,
    ChatMemberUpdatedFilter(member_status_changed=MEMBER),
)


# ---------------------------------------------------------------------------
# Funzione principale di bootstrap
# ---------------------------------------------------------------------------
async def main() -> None:
    maybe_log_public_ip()

    setup_scheduler(
        scheduler,
        maintenance_service=maintenance_service,
        mission_service=mission_service,
        identity_service=identity_service,
        reward_service=reward_service,
        statistics_service=statistics_service,
        profile_auto_sync_minutes=PROFILE_AUTO_SYNC_INTERVAL_MINUTES,
        logger=logger,
    )

    await maintenance_service.prepopulate_users()
    await identity_service.refresh_linked_profiles()

    try:
        await notification_service.send_startup_notification()
        await notification_service.send_bot_status_update(
            "AVVIATO",
            "Bot inizializzato correttamente con sistemi di sicurezza attivi."
            f" Gruppi autorizzati: {len(AUTHORIZED_GROUPS)}",
        )
    except Exception as exc:  # pragma: no cover - solo log
        bot_logger.log_error(exc, "Errore invio notifica avvio bot")

    logger.info("Avvio del bot.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
