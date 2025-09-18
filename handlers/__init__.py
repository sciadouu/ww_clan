"""Router composition for user-facing flows."""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

from aiogram import Bot, Dispatcher

from reward_service import RewardService
from services.db_manager import MongoManager
from services.identity_service import IdentityService
from services.mission_service import MissionService
from services.notification_service import EnhancedNotificationService

from .admin import create_admin_router
from .balances import BalancesHandlers
from .clan import ClanHandlers
from .member_search import MemberSearchHandlers
from .menu import MenuHandlers
from .missions import MissionHandlers
from .profile_link import ProfileLinkHandlers
from .rewards import RewardHandlers


def register_user_flow_handlers(
    dispatcher: Dispatcher,
    *,
    bot: Bot,
    logger,
    mission_service: MissionService,
    db_manager: MongoManager,
    identity_service: IdentityService,
    notification_service: EnhancedNotificationService,
    wolvesville_api_key: str,
    clan_id: str,
    skip_image_path: str,
    admin_ids: Sequence[int],
    authorized_groups: Sequence[int],
    schedule_admin_notification: Callable[..., None],
    reward_service: RewardService,
) -> None:
    """Instantiate feature routers and register them on the dispatcher."""

    mission_handlers = MissionHandlers(
        clan_id=clan_id,
        wolvesville_api_key=wolvesville_api_key,
        skip_image_path=skip_image_path,
        logger=logger,
    )

    balances_handlers = BalancesHandlers(
        db_manager=db_manager,
        bot=bot,
        admin_ids=admin_ids,
        logger=logger,
    )

    clan_handlers = ClanHandlers(
        wolvesville_api_key=wolvesville_api_key,
        logger=logger,
    )

    member_handlers = MemberSearchHandlers(
        wolvesville_api_key=wolvesville_api_key,
        clan_id=clan_id,
        logger=logger,
    )

    profile_link_handlers = ProfileLinkHandlers(
        identity_service=identity_service,
        db_manager=db_manager,
        clan_id=clan_id,
        wolvesville_api_key=wolvesville_api_key,
        schedule_admin_notification=schedule_admin_notification,
        logger=logger,
    )

    menu_handlers = MenuHandlers(
        bot=bot,
        logger=logger,
        mission_flow=mission_handlers.start_flow,
        balances_view=balances_handlers.show_balances,
        clan_flow=clan_handlers.start_saved_clan_flow,
        mission_participants=mission_service.partecipanti_command,
        member_check_flow=member_handlers.start_member_question,
    )

    reward_handlers = RewardHandlers(
        reward_service=reward_service,
        logger=logger,
    )

    routers: Iterable = (
        create_admin_router(
            bot=bot,
            notification_service=notification_service,
            authorized_groups=set(authorized_groups),
            schedule_admin_notification=schedule_admin_notification,
            logger=logger,
        ),
        mission_handlers.router,
        balances_handlers.router,
        clan_handlers.router,
        member_handlers.router,
        profile_link_handlers.router,
        menu_handlers.router,
        reward_handlers.router,
    )

    for router in routers:
        dispatcher.include_router(router)
