"""Strato di accesso ai dati centralizzato per il bot Wolvesville."""

from __future__ import annotations

from datetime import datetime, timezone
import re
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
        self.player_profiles_col: AsyncIOMotorCollection = self._database["player_profiles"]

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

    @staticmethod
    def _merge_donation_maps(*donation_maps: Optional[Dict[str, Any]]) -> Dict[str, int]:
        """Unisce più mappe di donazioni sommando i valori numerici."""

        merged: Dict[str, int] = {}
        for donations in donation_maps:
            if not donations:
                continue
            for currency, value in donations.items():
                try:
                    merged[currency] = merged.get(currency, 0) + int(value or 0)
                except (TypeError, ValueError):
                    continue
        return merged

    async def migrate_user_record(self, old_username: str, new_username: str) -> Dict[str, Any]:
        """Rinomina o unisce i documenti utente quando cambia l'username di gioco."""

        if not old_username or not new_username or old_username == new_username:
            return {"status": "unchanged"}

        old_doc = await self.users_col.find_one({"username": old_username})
        if not old_doc:
            return {"status": "missing_old"}

        new_doc = await self.users_col.find_one({"username": new_username})
        if not new_doc:
            await self.users_col.update_one(
                {"_id": old_doc["_id"]},
                {"$set": {"username": new_username}},
            )
            return {"status": "renamed", "updated_id": str(old_doc["_id"])}

        merged_donations = self._merge_donation_maps(
            old_doc.get("donazioni"), new_doc.get("donazioni")
        )
        merged_reward_points = 0
        for doc in (old_doc, new_doc):
            try:
                merged_reward_points += int(doc.get("reward_points", 0) or 0)
            except (TypeError, ValueError):
                continue

        achievements = set()
        for doc in (old_doc, new_doc):
            for achievement in doc.get("achievements", []) or []:
                if achievement:
                    achievements.add(str(achievement))

        update_payload: Dict[str, Any] = {"donazioni": merged_donations}
        if achievements:
            update_payload["achievements"] = sorted(achievements)
        update_payload["reward_points"] = merged_reward_points

        await self.users_col.update_one(
            {"_id": new_doc["_id"]},
            {"$set": update_payload},
        )
        await self.users_col.delete_one({"_id": old_doc["_id"]})

        return {
            "status": "merged",
            "deleted_old_id": str(old_doc["_id"]),
            "kept_id": str(new_doc["_id"]),
        }

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
        telegram_id: Optional[int] = None,
        telegram_username: Optional[str] = None,
        profile_snapshot: Optional[Dict[str, Any]] = None,
        original_username: Optional[str] = None,
        match_source: Optional[str] = None,
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
        if original_username:
            entry["original_username"] = original_username
        if telegram_id is not None:
            try:
                entry["telegram_id"] = int(telegram_id)
            except (TypeError, ValueError):
                pass
        if telegram_username:
            entry["telegram_username"] = telegram_username
        if match_source:
            entry["identity_match"] = match_source
        if profile_snapshot:
            entry["profile_snapshot"] = profile_snapshot
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
        participants: Sequence[Any],
        participants: Sequence[str],
        *,
        cost_per_participant: int,
        outcome: str = "processed",
        source: str = "manual",
        occurred_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Registra la partecipazione a una missione in una collezione dedicata."""

        processed_time = occurred_at or datetime.now(timezone.utc)
        event_id = f"{mission_id or 'mission'}-{uuid4()}"
        participant_entries: List[Dict[str, Any]] = []
        for participant in participants:
            entry: Dict[str, Any]
            if isinstance(participant, dict):
                username_value = (participant.get("username") or "").strip()
                if not username_value:
                    continue
                entry = {"username": username_value, "cost": cost_per_participant}
                original_username = participant.get("original_username")
                if original_username:
                    entry["original_username"] = original_username
                telegram_id = participant.get("telegram_id")
                if telegram_id is not None:
                    try:
                        entry["telegram_id"] = int(telegram_id)
                    except (TypeError, ValueError):
                        pass
                telegram_username = participant.get("telegram_username")
                if telegram_username:
                    entry["telegram_username"] = telegram_username
                match_source = participant.get("match")
                if match_source:
                    entry["identity_match"] = match_source
                profile_snapshot = participant.get("profile_snapshot")
                if profile_snapshot:
                    entry["profile_snapshot"] = profile_snapshot
            else:
                username_value = str(participant).strip()
                if not username_value:
                    continue
                entry = {
                    "username": username_value,
                    "original_username": username_value,
                    "cost": cost_per_participant,
                }
            participant_entries.append(entry)

        if not participant_entries:
            return None

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
            "participant_count": len(participant_entries),
            "outcome": outcome,
            "source": source,
            "cost_per_participant": cost_per_participant,
            "total_cost": cost_per_participant * len(participant_entries),
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

    async def get_profile_by_telegram_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Recupera il profilo collegato a un determinato Telegram ID."""

        if not telegram_id:
            return None
        return await self.player_profiles_col.find_one({"telegram_id": telegram_id})

    async def get_profile_by_game_username(self, game_username: str) -> Optional[Dict[str, Any]]:
        """Recupera il profilo a partire dallo username di gioco."""

        if not game_username:
            return None
        normalized = game_username.strip().lower()
        return await self.player_profiles_col.find_one({"game_username_lower": normalized})

    async def resolve_profile_by_game_alias(
        self, game_username: str
    ) -> Optional[Dict[str, Any]]:
        """Risolvi un profilo partendo da uno username o da un alias storico."""

        if not game_username:
            return None

        normalized_username = game_username.strip()
        if not normalized_username:
            return None

        normalized_lower = normalized_username.lower()

        profile = await self.player_profiles_col.find_one(
            {"game_username_lower": normalized_lower}
        )
        if profile:
            return {
                "profile": profile,
                "resolved_username": profile.get("game_username")
                or normalized_username,
                "match": "current",
            }

        profile = await self.player_profiles_col.find_one(
            {"game_username_history": {"$elemMatch": {"username_lower": normalized_lower}}}
        )
        if profile:
            return {
                "profile": profile,
                "resolved_username": profile.get("game_username")
                or normalized_username,
                "match": "history",
            }

        regex = re.compile(rf"^{re.escape(normalized_username)}$", re.IGNORECASE)
        profile = await self.player_profiles_col.find_one(
            {"game_username_history": {"$elemMatch": {"username": regex}}}
        )
        if profile:
            return {
                "profile": profile,
                "resolved_username": profile.get("game_username")
                or normalized_username,
                "match": "history",
            }

        return None

    async def sync_telegram_metadata(
        self,
        telegram_id: int,
        *,
        telegram_username: Optional[str],
        full_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Aggiorna le informazioni Telegram e traccia lo storico delle modifiche."""

        if not telegram_id:
            return None

        now = datetime.now(timezone.utc)
        normalized_username = telegram_username.lower() if telegram_username else None
        clean_full_name = full_name.strip() if full_name else None

        profile = await self.player_profiles_col.find_one({"telegram_id": telegram_id})
        if profile is None:
            document: Dict[str, Any] = {
                "_id": str(telegram_id),
                "telegram_id": telegram_id,
                "telegram_username": telegram_username,
                "telegram_username_lower": normalized_username,
                "telegram_username_history": [],
                "game_username": None,
                "game_username_lower": None,
                "game_username_history": [],
                "created_at": now,
                "updated_at": now,
            }
            if clean_full_name:
                document["full_name"] = clean_full_name
            if telegram_username:
                document["telegram_username_history"].append({
                    "username": telegram_username,
                    "username_lower": normalized_username,
                    "set_at": now,
                })
            await self.player_profiles_col.insert_one(document)
            return {"created": True, "profile": document}

        update_doc: Dict[str, Any] = {"updated_at": now}
        push_ops: Dict[str, Any] = {}
        changes: Dict[str, Any] = {}

        if telegram_username is not None and telegram_username != profile.get("telegram_username"):
            changes["telegram_username_changed"] = True
            changes["previous_telegram_username"] = profile.get("telegram_username")
            update_doc["telegram_username"] = telegram_username
            update_doc["telegram_username_lower"] = normalized_username
            push_ops["telegram_username_history"] = {
                "username": telegram_username,
                "username_lower": normalized_username,
                "set_at": now,
            }

        if clean_full_name and clean_full_name != profile.get("full_name"):
            update_doc["full_name"] = clean_full_name

        if not changes and len(update_doc) == 1:  # solo updated_at
            return None

        update_operations: Dict[str, Any] = {"$set": update_doc}
        if push_ops:
            update_operations["$push"] = {
                field: {"$each": [value]} for field, value in push_ops.items()
            }

        await self.player_profiles_col.update_one(
            {"_id": profile["_id"]},
            update_operations,
        )

        if changes:
            profile = await self.player_profiles_col.find_one({"telegram_id": telegram_id})
            changes["profile"] = profile
            return changes
        return None

    async def link_player_profile(
        self,
        telegram_id: int,
        *,
        game_username: str,
        telegram_username: Optional[str],
        full_name: Optional[str] = None,
        wolvesville_id: Optional[str] = None,
        verified: bool = False,
        verification_code: Optional[str] = None,
        verification_method: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Collega un profilo Telegram a uno username Wolvesville, tracciando gli storici."""

        normalized_username = game_username.strip()
        if not normalized_username:
            raise ValueError("game_username richiesto")

        now = datetime.now(timezone.utc)
        normalized_lower = normalized_username.lower()
        clean_full_name = full_name.strip() if full_name else None
        normalized_telegram_lower = telegram_username.lower() if telegram_username else None

        existing_profile = await self.player_profiles_col.find_one({"telegram_id": telegram_id})

        existing_by_username = await self.player_profiles_col.find_one(
            {"game_username_lower": normalized_lower}
        )
        if (
            existing_by_username
            and existing_by_username.get("telegram_id") != telegram_id
        ):
            return {
                "conflict": True,
                "reason": "game_username",
                "conflicting_profile": existing_by_username,
            }

        existing_by_wolvesville_id: Optional[Dict[str, Any]] = None
        if wolvesville_id:
            existing_by_wolvesville_id = await self.player_profiles_col.find_one(
                {"wolvesville_id": wolvesville_id}
            )
            if (
                existing_by_wolvesville_id
                and existing_by_wolvesville_id.get("telegram_id") != telegram_id
            ):
                return {
                    "conflict": True,
                    "reason": "wolvesville_id",
                    "conflicting_profile": existing_by_wolvesville_id,
                }

        update_doc: Dict[str, Any] = {
            "telegram_id": telegram_id,
            "game_username": normalized_username,
            "game_username_lower": normalized_lower,
            "updated_at": now,
        }
        if telegram_username is not None:
            update_doc["telegram_username"] = telegram_username
            update_doc["telegram_username_lower"] = normalized_telegram_lower
        if clean_full_name:
            update_doc["full_name"] = clean_full_name
        if wolvesville_id:
            update_doc["wolvesville_id"] = wolvesville_id

        set_on_insert: Dict[str, Any] = {
            "_id": str(telegram_id),
            "telegram_id": telegram_id,
            "created_at": now,
            "telegram_username_history": [],
            "game_username_history": [],
            "verification_history": [],
        }

        push_ops: Dict[str, Any] = {}
        changes: Dict[str, Any] = {
            "created": False,
            "game_username_changed": False,
            "previous_game_username": None,
            "telegram_username_changed": False,
            "previous_telegram_username": None,
            "migrate_result": None,
        }

        if existing_profile is None:
            changes["created"] = True
            if normalized_username:
                push_ops["game_username_history"] = {
                    "username": normalized_username,
                    "username_lower": normalized_lower,
                    "set_at": now,
                }
            if telegram_username:
                push_ops["telegram_username_history"] = {
                    "username": telegram_username,
                    "username_lower": normalized_telegram_lower,
                    "set_at": now,
                }
        else:
            if normalized_username != existing_profile.get("game_username"):
                changes["game_username_changed"] = True
                changes["previous_game_username"] = existing_profile.get("game_username")
                push_ops["game_username_history"] = {
                    "username": normalized_username,
                    "username_lower": normalized_lower,
                    "set_at": now,
                }
            if (
                telegram_username is not None
                and telegram_username != existing_profile.get("telegram_username")
            ):
                changes["telegram_username_changed"] = True
                changes["previous_telegram_username"] = existing_profile.get(
                    "telegram_username"
                )
                push_ops["telegram_username_history"] = {
                    "username": telegram_username,
                    "username_lower": normalized_telegram_lower,
                    "set_at": now,
                }

        verification_payload: Optional[Dict[str, Any]] = None
        if verified:
            verification_payload = {
                "status": "verified",
                "verified_at": now,
                "method": verification_method or "manual",
            }
            if verification_code:
                verification_payload["code"] = verification_code
            update_doc["verification"] = verification_payload
            push_ops["verification_history"] = verification_payload

        update_operations: Dict[str, Any] = {"$set": update_doc, "$setOnInsert": set_on_insert}
        if push_ops:
            update_operations["$push"] = {
                field: {"$each": [value]} for field, value in push_ops.items()
            }

        await self.player_profiles_col.update_one(
            {"telegram_id": telegram_id},
            update_operations,
            upsert=True,
        )

        profile = await self.player_profiles_col.find_one({"telegram_id": telegram_id})

        if changes["game_username_changed"] and changes["previous_game_username"]:
            changes["migrate_result"] = await self.migrate_user_record(
                changes["previous_game_username"], normalized_username
            )

        changes["profile"] = profile
        if verification_payload is not None:
            changes["verification"] = verification_payload

        return changes
