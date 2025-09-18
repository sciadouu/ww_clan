"""
Services package per il bot Telegram Wolvesville
Contiene servizi per notifiche, API, statistiche, calendar, etc.
"""

from .identity_service import IdentityService
from .maintenance_service import MaintenanceService
from .mission_service import MissionService
from .notification_service import NotificationService

__all__ = [
    "NotificationService",
    "IdentityService",
    "MaintenanceService",
    "MissionService",
]
