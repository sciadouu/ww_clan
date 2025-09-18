"""Repository dedicato alla gestione dei punti ricompensa."""

from __future__ import annotations
from dataclasses import dataclass, field
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ReturnDocument

from services.db_manager import MongoManager


def _ensure_timezone(value: datetime) -> datetime:
    """Rende timezone aware i datetime salvati in cronologia."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(slots=True)
class RewardsRepository:
    """Incapsula l'accesso ai dati per il sistema premi."""

    db_manager: MongoManager
    _users: AsyncIOMotorCollection = field(init=False, repr=False)
    _history: AsyncIOMotorCollection = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._users: AsyncIOMotorCollection = self.db_manager.users_col
        self._history: AsyncIOMotorCollection = self.db_manager.rewards_history_col

    # ------------------------------------------------------------------
    # Operazioni di scrittura
    # ------------------------------------------------------------------
    async def increment_points(
        self,
        username: str,
        points: int,
        *,
        point_type: str,
        amount: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Incrementa i punti di un utente e registra l'evento in cronologia."""

        if not username or points == 0:
            return {"acknowledged": False, "new_total": None}

        now = datetime.now(timezone.utc)
        document = await self._users.find_one_and_update(
            {"username": username},
            {
                "$inc": {"reward_points": points},
                "$setOnInsert": {"username": username, "reward_points": 0},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        running_total = int(document.get("reward_points", 0)) if document else points

        history_payload: Dict[str, Any] = {
            "username": username,
            "event_type": "points",
            "point_type": point_type,
            "points": points,
            "amount": amount,
            "metadata": metadata or {},
            "running_total": running_total,
            "created_at": now,
        }

        await self._history.insert_one(history_payload)

        return {
            "acknowledged": True,
            "new_total": running_total,
            "history_event": history_payload,
        }

    async def append_achievement(
        self,
        username: str,
        achievement_code: str,
        details: Dict[str, Any],
    ) -> bool:
        """Memorizza un nuovo achievement e lo aggiunge alla cronologia."""

        if not username or not achievement_code:
            return False

        existing = await self._users.find_one({"username": username})
        if existing and achievement_code in (existing.get("achievements") or []):
            return False

        update_result = await self._users.find_one_and_update(
            {"username": username},
            {
                "$addToSet": {"achievements": achievement_code},
                "$setOnInsert": {"username": username, "reward_points": 0},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        if not update_result:
            return False

        achievements: Sequence[str] = update_result.get("achievements", []) or []
        if achievement_code not in achievements:
            return False

        running_total = int(update_result.get("reward_points", 0) or 0)
        payload = {
            "username": username,
            "event_type": "achievement",
            "achievement": {
                "code": achievement_code,
                **details,
            },
            "points": 0,
            "running_total": running_total,
            "created_at": datetime.now(timezone.utc),
        }

        await self._history.insert_one(payload)
        return True

    # ------------------------------------------------------------------
    # Operazioni di lettura
    # ------------------------------------------------------------------
    async def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Recupera il documento utente completo."""

        if not username:
            return None
        return await self._users.find_one({"username": username})

    async def resolve_username(self, username: str) -> Optional[str]:
        """Risolvi eventuali alias utilizzando l'Identity Service."""

        if not username:
            return None

        normalized = username.strip()
        if not normalized:
            return None

        resolution = await self.db_manager.resolve_profile_by_game_alias(normalized)
        if resolution and resolution.get("resolved_username"):
            return resolution["resolved_username"]
        return normalized

    async def get_user_point_breakdown(self, username: str) -> Dict[str, Any]:
        """Restituisce la suddivisione dei punti per tipologia."""

        if not username:
            return {"total_events": 0, "total_points": 0, "by_type": {}}

        pipeline = [
            {"$match": {"username": username, "event_type": "points"}},
            {
                "$group": {
                    "_id": "$point_type",
                    "points": {"$sum": "$points"},
                    "events": {"$sum": 1},
                    "total_amount": {"$sum": {"$ifNull": ["$amount", 0]}},
                    "last_event": {"$max": "$created_at"},
                }
            },
        ]

        entries = await self._history.aggregate(pipeline).to_list(length=None)
        by_type: Dict[str, Any] = {}
        total_points = 0
        total_events = 0

        for entry in entries:
            point_type = entry.get("_id")
            if not point_type:
                continue
            points_value = int(entry.get("points", 0) or 0)
            events_value = int(entry.get("events", 0) or 0)
            total_points += points_value
            total_events += events_value
            by_type[point_type] = {
                "points": points_value,
                "events": events_value,
                "total_amount": entry.get("total_amount", 0),
                "last_event": entry.get("last_event"),
            }

        return {
            "total_points": total_points,
            "total_events": total_events,
            "by_type": by_type,
        }

    async def get_leaderboard(
        self,
        *,
        limit: int = 10,
        period_start: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Restituisce la classifica in base all'intervallo richiesto."""

        limit = max(int(limit), 1)

        if period_start is None:
            cursor = (
                self._users.find({"reward_points": {"$gt": 0}})
                .sort("reward_points", -1)
                .limit(limit)
            )
            users = await cursor.to_list(length=None)
            return [
                {
                    "username": doc.get("username"),
                    "total_points": int(doc.get("reward_points", 0) or 0),
                    "period_points": int(doc.get("reward_points", 0) or 0),
                    "achievements": doc.get("achievements", []),
                }
                for doc in users
                if doc.get("username")
            ]

        match_stage: Dict[str, Any] = {
            "username": {"$ne": None},
            "event_type": "points",
            "created_at": {"$gte": _ensure_timezone(period_start)},
        }

        pipeline = [
            {"$match": match_stage},
            {
                "$group": {
                    "_id": "$username",
                    "period_points": {"$sum": "$points"},
                    "last_event": {"$max": "$created_at"},
                }
            },
            {"$sort": {"period_points": -1, "last_event": 1}},
            {"$limit": limit},
        ]

        leaderboard = await self._history.aggregate(pipeline).to_list(length=None)
        usernames = [entry.get("_id") for entry in leaderboard if entry.get("_id")]

        if not usernames:
            return []

        user_docs = await self._users.find({"username": {"$in": usernames}}).to_list(length=None)
        user_map = {doc.get("username"): doc for doc in user_docs}

        enriched: List[Dict[str, Any]] = []
        for entry in leaderboard:
            username = entry.get("_id")
            if not username:
                continue
            user_doc = user_map.get(username, {})
            enriched.append(
                {
                    "username": username,
                    "period_points": int(entry.get("period_points", 0) or 0),
                    "total_points": int(user_doc.get("reward_points", 0) or 0),
                    "achievements": user_doc.get("achievements", []),
                    "last_event": entry.get("last_event"),
                }
            )

        return enriched

    async def get_user_progress(
        self,
        username: str,
        *,
        period_start: Optional[datetime] = None,
        history_limit: int = 5,
    ) -> Optional[Dict[str, Any]]:
        """Recupera un riepilogo dei progressi dell'utente."""

        if not username:
            return None

        user_doc = await self.get_user(username)
        if not user_doc:
            return None

        history_cursor = (
            self._history.find({"username": username})
            .sort("created_at", -1)
            .limit(max(int(history_limit), 1))
        )
        history = await history_cursor.to_list(length=None)

        period_points: Optional[int] = None
        if period_start is not None:
            pipeline = [
                {
                    "$match": {
                        "username": username,
                        "event_type": "points",
                        "created_at": {"$gte": _ensure_timezone(period_start)},
                    }
                },
                {"$group": {"_id": None, "points": {"$sum": "$points"}}},
            ]
            aggregation = await self._history.aggregate(pipeline).to_list(length=None)
            if aggregation:
                period_points = int(aggregation[0].get("points", 0) or 0)

        breakdown = await self.get_user_point_breakdown(username)

        achievement_events = [
            event
            for event in history
            if event.get("event_type") == "achievement"
        ]

        return {
            "username": username,
            "total_points": int(user_doc.get("reward_points", 0) or 0),
            "achievements": user_doc.get("achievements", []),
            "history": history,
            "period_points": period_points,
            "breakdown": breakdown,
            "achievement_events": achievement_events,
        }

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    @staticmethod
    def compute_period_start(period: str) -> Optional[datetime]:
        """Restituisce l'inizio del periodo richiesto."""

        normalized = (period or "").strip().lower()
        now = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if normalized in {"", "all", "totale", "sempre", "overall"}:
            return None
        if normalized in {"day", "daily", "giorno", "giornaliera"}:
            return today
        if normalized in {"week", "weekly", "settimana", "settimanale"}:
            start_of_week = today - timedelta(days=today.weekday())
            return start_of_week
        if normalized in {"month", "monthly", "mese", "mensile"}:
            return today.replace(day=1)

        return None
