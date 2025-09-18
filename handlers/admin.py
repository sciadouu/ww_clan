"""Administrative routers handling chat membership events."""

from __future__ import annotations

from typing import Callable, Set

from aiogram import Bot, Router
from aiogram.filters import ChatMemberUpdatedFilter, KICKED, MEMBER
from aiogram.types import ChatMemberUpdated

from services.notification_service import (
    EnhancedNotificationService,
    NotificationType,
)


def create_admin_router(
    *,
    bot: Bot,
    notification_service: EnhancedNotificationService,
    authorized_groups: Set[int],
    schedule_admin_notification: Callable[[str], None],
    logger,
) -> Router:
    """Return a router that reacts to bot joins and removals from chats."""

    router = Router()

    @router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=KICKED))
    async def bot_kicked_from_chat(event: ChatMemberUpdated) -> None:
        chat_id = event.chat.id
        chat_title = event.chat.title or "Chat Privato"

        logger.info("Bot rimosso dalla chat: %s (%s)", chat_id, chat_title)

        if chat_id in authorized_groups:
            message = (
                "⚠️ **BOT RIMOSSO DA GRUPPO AUTORIZZATO**\n\n"
                f"👥 **Gruppo:** {chat_title}\n"
                f"🆔 **Chat ID:** `{chat_id}`\n\n"
                "🔄 **Azione:** Verificare se l'uscita è intenzionale"
            )
            schedule_admin_notification(
                message,
                notification_type=NotificationType.WARNING,
                urgent=True,
            )

    @router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=MEMBER))
    async def bot_added_to_chat(event: ChatMemberUpdated) -> None:
        chat_id = event.chat.id
        chat_title = event.chat.title or "Chat Privato"
        user_id = event.from_user.id if event.from_user else None

        logger.info("Bot aggiunto alla chat: %s (%s)", chat_id, chat_title)

        if chat_id not in authorized_groups:
            if notification_service.is_group_blacklisted(chat_id):
                logger.info("Gruppo %s in blacklist, uscita immediata", chat_id)
                try:
                    await bot.leave_chat(chat_id)
                except Exception as exc:  # pragma: no cover - solo logging
                    logger.error(
                        "Errore nell'uscire dal gruppo blacklistato %s: %s",
                        chat_id,
                        exc,
                    )
                return

            await notification_service.handle_unauthorized_group_join(
                chat_id=chat_id,
                chat_title=chat_title,
                user_id=user_id,
            )

            try:
                await bot.leave_chat(chat_id)
                logger.info("Uscito dal gruppo non autorizzato: %s", chat_id)
            except Exception as exc:  # pragma: no cover - solo logging
                logger.error(
                    "Errore nell'uscire dal gruppo non autorizzato %s: %s",
                    chat_id,
                    exc,
                )
        else:
            await notification_service.send_authorized_group_notification(
                chat_id, chat_title
            )
            logger.info("Bot aggiunto con successo al gruppo autorizzato: %s", chat_id)

    return router
