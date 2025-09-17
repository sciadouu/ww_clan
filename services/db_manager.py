"""Strato di accesso ai dati centralizzato per il bot Wolvesville."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)


class MongoManager:
    """Incapsula l'accesso alle principali collezioni MongoDB."""

    def __init__(self, client: AsyncIOMotorClient, database_name: str) -> None:
        self._client = client
        self._database: AsyncIOMotorDatabase = client[database_name]

        self.users_col: AsyncIOMotorCollection = self._database["users"]
        self.processed_ledger_col: AsyncIOMotorCollection = self._database["processed_ledger"]
        self.processed_active_missions_col: AsyncIOMotorCollection = self._database[
            "processed_active_missions"
        ]
        self.donation_history_col: AsyncIOMotorCollection = self._database["donation_history"]
        self.missions_history_col: AsyncIOMotorCollection = self._database["missions_history"]

    @property
    def database(self) -> AsyncIOMotorDatabase:
        """Restituisce il database MongoDB sottostante."""

        return self._database

    @staticmethod
    def _normalize_currency(currency: str) -> str:
        mapping = {
            "gold": "Oro",
            "oro": "Oro",
            "gem": "Gem",
            "gems": "Gem",
            "gemme": "Gem",
        }
        return mapping.get(currency.lower(), currency.capitalize())

    def normalize_currency(self, currency: str) -> str:
        """Espone la normalizzazione delle valute."""

        return self._normalize_currency(currency)

    async def ensure_user(self, username: str) -> bool:
        """Crea l'utente se non esiste, restituendo True se inserito."""

        if not username:
            return False

        result = await self.users_col.update_one(
            {"username": username},
            {"$setOnInsert": {"donazioni": {"Oro": 0, "Gem": 0}, "username": username}},
            upsert=True,
        )
        return bool(getattr(result, "upserted_id", None))

    async def update_user_balance(self, username: str, currency: str, amount: int) -> str:
        """Incrementa il bilancio di un utente per la valuta indicata."""

        normalized = self._normalize_currency(currency)
        field = f"donazioni.{normalized}"
        await self.users_col.update_one(
            {"username": username},
            {"$inc": {field: amount}, "$setOnInsert": {"username": username}},
            upsert=True,
        )
        return normalized

    async def set_user_currency(self, username: str, currency: str, value: int) -> str:
        """Imposta il valore assoluto di una valuta per l'utente."""

        normalized = self._normalize_currency(currency)
        field = f"donazioni.{normalized}"
        await self.users_col.update_one(
            {"username": username},
            {"$set": {field: value}, "$setOnInsert": {"username": username}},
            upsert=True,
        )
        return normalized

    async def list_users(self) -> List[Dict[str, Any]]:
        """Restituisce tutti gli utenti presenti nella collezione."""

        return await self.users_col.find({}).to_list(length=None)

    async def remove_duplicate_users(self) -> List[Dict[str, Any]]:
        """Elimina eventuali duplicati e restituisce un riepilogo delle rimozioni."""

        pipeline = [
            {"$group": {"_id": "$username", "count": {"$sum": 1}, "docs": {"$push": "$_id"}}},
            {"$match": {"count": {"$gt": 1}}},
        ]
        duplicates = await self.users_col.aggregate(pipeline).to_list(length=None)

        removed_info: List[Dict[str, Any]] = []
        for duplicate in duplicates:
            username = duplicate.get("_id")
            doc_ids = duplicate.get("docs", [])
            docs_to_delete = doc_ids[1:]
            if not docs_to_delete:
                continue

            delete_result = await self.users_col.delete_many({"_id": {"$in": docs_to_delete}})
            removed_info.append(
                {
                    "username": username,
                    "removed": delete_result.deleted_count,
                    "removed_ids": docs_to_delete,
                }
            )

        return removed_info

    async def remove_user_by_username(self, username: str) -> int:
        """Rimuove un utente in base all'username e restituisce il conteggio eliminato."""

        if not username:
            return 0
        result = await self.users_col.delete_one({"username": username})
        return result.deleted_count

    async def has_processed_ledger(self, record_id: str) -> bool:
        """Verifica se un record del ledger è già stato processato."""

        if not record_id:
            return False
        doc = await self.processed_ledger_col.find_one({"_id": record_id})
        return doc is not None

    async def mark_ledger_processed(
        self,
        record_id: str,
        *,
        raw_record: Optional[Dict[str, Any]] = None,
        processed_at: Optional[datetime] = None,
    ) -> None:
        """Memorizza l'elaborazione di un record del ledger."""

        if not record_id:
            return
        processed_time = processed_at or datetime.now(timezone.utc)
        payload: Dict[str, Any] = {"_id": record_id, "processed_at": processed_time}
        if raw_record is not None:
            payload["raw"] = raw_record
        await self.processed_ledger_col.replace_one({"_id": record_id}, payload, upsert=True)

    async def log_donation(
        self,
        record_id: str,
        username: str,
        gold_amount: int,
        gems_amount: int,
        *,
        raw_record: Optional[Dict[str, Any]] = None,
        processed_at: Optional[datetime] = None,
    ) -> None:
        """Archivia il dettaglio di una donazione elaborata."""

        if not record_id or not username:
            return
        processed_time = processed_at or datetime.now(timezone.utc)
        entry: Dict[str, Any] = {
            "_id": record_id,
            "username": username,
            "gold": int(gold_amount or 0),
            "gems": int(gems_amount or 0),
            "processed_at": processed_time,
        }
        if raw_record is not None:
            entry["raw"] = raw_record
        await self.donation_history_col.replace_one({"_id": record_id}, entry, upsert=True)

    async def has_processed_active_mission(self, mission_id: str) -> bool:
        """Controlla se una missione attiva è già stata processata."""

        if not mission_id:
            return False
        doc = await self.processed_active_missions_col.find_one({"_id": mission_id})
        return doc is not None

    async def mark_active_mission_processed(
        self,
        mission_id: str,
        tier_start_time: Optional[str] = None,
        *,
        processed_at: Optional[datetime] = None,
    ) -> None:
        """Segna una missione attiva come elaborata."""

        if not mission_id:
            return
        processed_time = processed_at or datetime.now(timezone.utc)
        payload: Dict[str, Any] = {
            "_id": mission_id,
            "processed_at": processed_time,
        }
        if tier_start_time is not None:
            payload["tierStartTime"] = tier_start_time
        await self.processed_active_missions_col.replace_one({"_id": mission_id}, payload, upsert=True)

    async def log_mission_participation(
        self,
        mission_id: Optional[str],
        mission_type: str,
        participants: Sequence[str],
        *,
        cost_per_participant: int,
        outcome: str = "processed",
        source: str = "manual",
        occurred_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Registra la partecipazione a una missione in una collezione dedicata."""

        if not participants:
            return None

        processed_time = occurred_at or datetime.now(timezone.utc)
        event_id = f"{mission_id or 'mission'}-{uuid4()}"
        participant_entries = [
            {"username": username, "cost": cost_per_participant} for username in participants
        ]
        document: Dict[str, Any] = {
            "_id": event_id,
            "mission_id": mission_id,
            "mission_type": mission_type,
            "participants": participant_entries,
            "participant_count": len(participants),
            "outcome": outcome,
            "source": source,
            "cost_per_participant": cost_per_participant,
            "total_cost": cost_per_participant * len(participants),
            "processed_at": processed_time,
        }
        if metadata:
            document["metadata"] = metadata

        result = await self.missions_history_col.insert_one(document)
        return str(result.inserted_id)

    async def fetch_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Recupera i dati di un utente dal database."""

        if not username:
            return None
        return await self.users_col.find_one({"username": username})

    async def increment_reward_points(self, username: str, points: int) -> None:
        """Incrementa i punti ricompensa di un utente."""

        if not username or points == 0:
            return
        await self.users_col.update_one(
            {"username": username},
            {"$inc": {"reward_points": points}, "$setOnInsert": {"username": username}},
            upsert=True,
        )

    async def add_achievements(self, username: str, achievements: Sequence[str]) -> None:
        """Aggiunge nuovi achievement all'utente, evitando duplicati."""

        achievements_list = list(achievements)
        if not username or not achievements_list:
            return
        await self.users_col.update_one(
            {"username": username},
            {"$addToSet": {"achievements": {"$each": achievements_list}}},
        )

    async def aggregate_users(self, pipeline: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Esegue un'aggregazione sugli utenti."""

        return await self.users_col.aggregate(list(pipeline)).to_list(length=None)
