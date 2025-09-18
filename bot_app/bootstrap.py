"""Funzioni di bootstrap e configurazione dell'applicazione del bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import motor.motor_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services.db_manager import MongoManager
from services.notification_service import EnhancedNotificationService
from services.rewards_repository import RewardsRepository


@dataclass(slots=True)
class BotAppContext:
    """Contenitore per le dipendenze condivise del bot."""

    bot: Bot
    dispatcher: Dispatcher
    notification_service: EnhancedNotificationService
    db_manager: MongoManager
    scheduler: AsyncIOScheduler
    mongo_client: motor.motor_asyncio.AsyncIOMotorClient
    rewards_repository: RewardsRepository


def _create_bot(token: str) -> Bot:
    """Crea l'istanza di :class:`aiogram.Bot` con la configurazione di default."""

    session = AiohttpSession()
    default_properties = DefaultBotProperties(parse_mode="HTML")
    return Bot(token=token, session=session, default=default_properties)


def _create_dispatcher() -> Dispatcher:
    """Crea un dispatcher con storage in memoria."""

    return Dispatcher(storage=MemoryStorage())


def _create_mongo_manager(
    uri: str, database_name: str
) -> Tuple[motor.motor_asyncio.AsyncIOMotorClient, MongoManager]:
    """Inizializza il client MongoDB e il relativo manager applicativo."""

    client = motor.motor_asyncio.AsyncIOMotorClient(
        uri,
        tlsAllowInvalidCertificates=True,
    )
    manager = MongoManager(client, database_name)
    return client, manager


def _create_notification_service(
    bot: Bot,
    admin_ids: Sequence[int],
    admin_channel_id: Optional[int],
    owner_id: Optional[int],
) -> EnhancedNotificationService:
    """Configura il servizio di notifiche amministrative."""

    return EnhancedNotificationService(
        bot=bot,
        admin_ids=list(admin_ids),
        admin_channel_id=admin_channel_id,
        owner_id=owner_id,
    )


def _create_scheduler(timezone: str) -> AsyncIOScheduler:
    """Restituisce uno scheduler asincrono configurato con il fuso richiesto."""

    return AsyncIOScheduler(timezone=timezone)


def create_app_context(
    *,
    token: str,
    mongo_uri: str,
    database_name: str,
    admin_ids: Sequence[int],
    admin_channel_id: Optional[int],
    owner_id: Optional[int],
    scheduler_timezone: str = "Europe/Rome",
) -> BotAppContext:
    """Crea e restituisce il contesto applicativo condiviso dal bot."""

    bot = _create_bot(token)
    dispatcher = _create_dispatcher()
    mongo_client, db_manager = _create_mongo_manager(mongo_uri, database_name)
    rewards_repository = RewardsRepository(db_manager=db_manager)
    notification_service = _create_notification_service(
        bot,
        admin_ids,
        admin_channel_id,
        owner_id,
    )
    scheduler = _create_scheduler(scheduler_timezone)

    return BotAppContext(
        bot=bot,
        dispatcher=dispatcher,
        notification_service=notification_service,
        db_manager=db_manager,
        scheduler=scheduler,
        mongo_client=mongo_client,
        rewards_repository=rewards_repository,
    )
