"""Centralized scheduler configuration for Wolvesville bot jobs."""

from __future__ import annotations

from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from reward_service import RewardService
from services.identity_service import IdentityService
from services.maintenance_service import MaintenanceService
from services.mission_service import MissionService


def setup_scheduler(
    scheduler: AsyncIOScheduler,
    *,
    maintenance_service: MaintenanceService,
    mission_service: MissionService,
    identity_service: IdentityService,
    reward_service: RewardService,
    profile_auto_sync_minutes: int,
    logger,
) -> AsyncIOScheduler:
    """Register periodic jobs executed by the shared scheduler."""

    scheduler.add_job(
        mission_service.send_weekly_mission_skin,
        "cron",
        day_of_week="mon",
        hour=8,
        minute=0,
        timezone="Europe/Rome",
    )

    scheduler.add_job(
        maintenance_service.process_ledger,
        "interval",
        minutes=5,
        next_run_time=datetime.now(),
    )

    scheduler.add_job(
        mission_service.process_active_mission_auto,
        "interval",
        minutes=5,
        next_run_time=datetime.now(),
    )

    scheduler.add_job(
        maintenance_service.prepopulate_users,
        "interval",
        days=3,
        next_run_time=datetime.now(),
    )

    scheduler.add_job(
        identity_service.refresh_linked_profiles,
        "interval",
        minutes=profile_auto_sync_minutes,
        next_run_time=datetime.now(),
    )

    scheduler.add_job(
        maintenance_service.clean_duplicate_users,
        "interval",
        hours=24,
        next_run_time=datetime.now(),
    )

    scheduler.add_job(
        maintenance_service.check_clan_departures,
        "interval",
        hours=6,
        next_run_time=datetime.now(),
    )

    scheduler.add_job(
        reward_service.publish_weekly_leaderboard,
        "cron",
        day_of_week="mon",
        hour=9,
        minute=0,
        timezone="Europe/Rome",
    )

    scheduler.add_job(
        reward_service.publish_monthly_leaderboard,
        "cron",
        day=1,
        hour=9,
        minute=5,
        timezone="Europe/Rome",
    )

    if not scheduler.running:
        scheduler.start()

    logger.info("Scheduler configurato con tutte le funzioni di manutenzione.")
    return scheduler

