"""Mission management service for Wolvesville bot."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.db_manager import MongoManager
from reward_service import RewardService
from services.identity_service import IdentityService
from services.maintenance_service import MaintenanceService


class MissionStates(StatesGroup):
    """Finite state machine for the /partecipanti flow."""

    SELECTING_MISSION = State()
    CONFIRMING_PARTICIPANTS = State()


@dataclass(slots=True)
class MissionService:
    """Coordinate Wolvesville mission processing and history logging."""

    bot: Bot
    db_manager: MongoManager
    identity_service: IdentityService
    maintenance_service: MaintenanceService
    wolvesville_api_key: str
    clan_id: str
    logger: logging.Logger
    clan_chat_id: Optional[int] = None
    clan_topic_id: Optional[int] = None
    reward_service: Optional[RewardService] = None
    authorized_group_ids: Sequence[int] = field(default_factory=tuple)
    owner_id: Optional[int] = None
    _authorized_group_ids: Set[int] = field(
        init=False, default_factory=set, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self._authorized_group_ids: Set[int] = {
            int(chat_id) for chat_id in (self.authorized_group_ids or [])
        }

    # ---------------------------------------------------------------------
    # Public API used by other components (scheduler, commands, services)
    # ---------------------------------------------------------------------
    async def _reward_mission_participants(
        self,
        identities: Sequence[Dict[str, Any]],
        *,
        mission_type: str,
        mission_id: Optional[str],
        source: str,
        outcome: str,
        metadata: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> None:
        """Assegna i punti premio ai partecipanti della missione."""

        if not self.reward_service or not identities:
            return

        outcome_normalized = (outcome or "").lower()
        success_outcomes = {
            "success",
            "completed",
            "complete",
            "victory",
            "auto_processed",
            "processed",
        }
        is_success = outcome_normalized in success_outcomes

        base_metadata: Dict[str, Any] = {
            "mission_id": mission_id,
            "mission_type": mission_type,
            "mission_source": source,
            "mission_outcome": outcome,
        }
        if metadata:
            base_metadata.update(metadata)
        if event_id:
            base_metadata["mission_event_id"] = event_id

        for identity in identities:
            username = identity.get("resolved_username")
            if not username:
                continue

            personal_metadata = dict(base_metadata)
            if identity.get("original_username"):
                personal_metadata["original_username"] = identity.get(
                    "original_username"
                )
            if identity.get("match"):
                personal_metadata["identity_match"] = identity.get("match")

            try:
                await self.reward_service.award_points(
                    username,
                    "MISSION_PARTICIPATION",
                    metadata=personal_metadata,
                )
                if is_success:
                    await self.reward_service.award_points(
                        username,
                        "MISSION_SUCCESS",
                        metadata={**personal_metadata, "success": True},
                    )
            except Exception as exc:  # pragma: no cover - log difensivo
                self.logger.warning(
                    "Assegnazione punti missione fallita per %s: %s", username, exc
                )

    async def process_mission(
        self,
        participants: Sequence[str],
        mission_type: str,
        *,
        mission_id: Optional[str] = None,
        outcome: str = "processed",
        source: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Apply mission costs, resolve participants and log the event."""

        if not participants:
            self.logger.info(
                "Processo missione %s saltato: nessun partecipante fornito.",
                mission_type,
            )
            return None

        mission_type = mission_type or "Unknown"
        mission_type_lower = mission_type.lower()

        resolved_identities: List[Dict[str, Any]] = []
        alias_resolved_count = 0
        unresolved_participants: List[str] = []

        for participant in participants:
            identity = await self.identity_service.resolve_member_identity(participant)
            resolved_username = identity.get("resolved_username")
            if not resolved_username:
                self.logger.warning(
                    "Missione %s: ignorato partecipante senza username valido (%s)",
                    mission_type,
                    participant,
                )
                unresolved_participants.append(participant)
                continue
            if (
                identity.get("match") == "history"
                and identity.get("original_username")
                and identity.get("original_username") != resolved_username
            ):
                alias_resolved_count += 1
                self.logger.info(
                    "Missione %s: alias risolto %s → %s",
                    mission_type,
                    identity.get("original_username"),
                    resolved_username,
                )
            resolved_identities.append(identity)

        if not resolved_identities:
            self.logger.info(
                "Processo missione %s saltato: nessun partecipante risolto.",
                mission_type,
            )
            return None

        original_participant_count = len(participants)
        participant_count = len(resolved_identities)
        unresolved_count = max(original_participant_count - participant_count, 0)

        event_timestamp = datetime.now(timezone.utc)

        cost = 0
        currency_key = mission_type

        if mission_type_lower == "gold":
            cost = 500
            currency_key = "Gold"
        elif mission_type_lower == "gem":
            if participant_count > 7:
                cost = 140
            elif 5 <= participant_count <= 7:
                cost = 150
            else:
                cost = 0
            currency_key = "Gem"

        if cost != 0:
            for identity in resolved_identities:
                await self.maintenance_service.update_user_balance(
                    identity["resolved_username"], currency_key, -cost
                )
            self.logger.info(
                "Applicato costo di %s %s a %s partecipanti (missione %s).",
                cost,
                "Oro" if mission_type_lower == "gold" else "Gem",
                participant_count,
                mission_type,
            )
            if alias_resolved_count:
                self.logger.info(
                    "Missione %s: %s partecipanti provenivano da alias storici.",
                    mission_type,
                    alias_resolved_count,
                )
        else:
            self.logger.info(
                "Registrata missione %s senza costi aggiuntivi per %s partecipanti.",
                mission_type,
                participant_count,
            )

        metadata_payload = dict(metadata or {})
        metadata_payload.setdefault("participants_count", original_participant_count)
        metadata_payload.setdefault("cost_applied", cost)
        metadata_payload["resolved_participants_count"] = participant_count
        metadata_payload["unresolved_participants_count"] = unresolved_count
        metadata_payload["alias_resolutions"] = alias_resolved_count
        metadata_payload["linked_participants"] = sum(
            1 for identity in resolved_identities if identity.get("telegram_id")
        )
        if unresolved_participants:
            metadata_payload["unresolved_participants"] = unresolved_participants

        participant_entries: List[Dict[str, Any]] = []
        for identity in resolved_identities:
            entry: Dict[str, Any] = {
                "username": identity.get("resolved_username"),
                "original_username": identity.get("original_username"),
            }
            if identity.get("telegram_id") is not None:
                entry["telegram_id"] = identity.get("telegram_id")
            if identity.get("telegram_username"):
                entry["telegram_username"] = identity.get("telegram_username")
            if identity.get("match"):
                entry["match"] = identity.get("match")
            if identity.get("profile_snapshot"):
                entry["profile_snapshot"] = identity.get("profile_snapshot")
            participant_entries.append(entry)

        event_id = await self.db_manager.log_mission_participation(
            mission_id,
            mission_type,
            participant_entries,
            list(participants),
            cost_per_participant=cost,
            outcome=outcome,
            source=source,
            occurred_at=event_timestamp,
            metadata=metadata_payload,
        )

        if event_id:
            self.logger.info(
                "Registrata partecipazione missione %s (event_id=%s) con %s partecipanti.",
                mission_id or "manual",
                event_id,
                participant_count,
            )

        await self._reward_mission_participants(
            resolved_identities,
            mission_type=mission_type,
            mission_id=mission_id,
            source=source,
            outcome=outcome,
            metadata=metadata_payload,
            event_id=event_id,
        )

        return event_id

    def _is_mission_enable_allowed(
        self, chat: Optional[types.Chat], user_id: Optional[int]
    ) -> bool:
        if chat is None:
            return False

        chat_type = getattr(chat, "type", "") or ""
        chat_id = getattr(chat, "id", None)

        if chat_type == "private":
            return bool(
                user_id is not None
                and self.owner_id is not None
                and user_id == self.owner_id
            )

        if chat_id is None:
            return False

        if self._authorized_group_ids:
            try:
                return int(chat_id) in self._authorized_group_ids
            except (TypeError, ValueError):
                return False

        return chat_type in {"group", "supergroup"}

    async def _handle_mission_enable_denied(
        self,
        *,
        message: Optional[types.Message],
        callback: Optional[types.CallbackQuery],
        user_id: Optional[int],
    ) -> None:
        warning_text = (
            "❌ Il comando /partecipanti può essere utilizzato solo nel gruppo del clan."
            " In privato è consentito esclusivamente all'owner."
        )

        if callback is not None:
            try:
                await callback.answer("Accesso negato", show_alert=True)
            except Exception:  # pragma: no cover - not critical if alert fails
                pass

        if message is not None:
            try:
                await message.answer(warning_text)
            except Exception:  # pragma: no cover - avoid breaking flow on send failure
                pass

            chat = getattr(message, "chat", None)
            chat_id = getattr(chat, "id", "unknown") if chat else "unknown"
            if user_id is not None:
                self.logger.warning(
                    "Mission participant enable denied for user %s in chat %s",
                    user_id,
                    chat_id,
                )

    async def capture_clan_context(self, message: types.Message) -> None:
        """Memorizza chat e topic del clan per gli annunci automatici."""

        chat = getattr(message, "chat", None)
        if chat is None:
            return

        chat_type = getattr(chat, "type", "") or ""
        if chat_type not in {"group", "supergroup"}:
            return

        chat_id = getattr(chat, "id", None)
        if chat_id is None:
            return

        thread_id_raw = getattr(message, "message_thread_id", None)
        thread_id: Optional[int]
        try:
            thread_id = int(thread_id_raw) if thread_id_raw is not None else None
        except (TypeError, ValueError):
            thread_id = None

        self.clan_chat_id = int(chat_id)
        self.clan_topic_id = thread_id

        try:
            await self.db_manager.upsert_mission_announcement_context(
                chat_id=int(chat_id), message_thread_id=thread_id
            )
        except Exception as exc:  # pragma: no cover - log difensivo
            self.logger.warning(
                "Impossibile salvare il contesto missione: %s", exc
            )

    async def _ensure_clan_context(self) -> bool:
        """Recupera da database la chat del clan se non è già nota."""

        if self.clan_chat_id is not None:
            return True

        try:
            record = await self.db_manager.get_mission_announcement_context()
        except Exception as exc:  # pragma: no cover - log difensivo
            self.logger.warning(
                "Recupero contesto missione fallito: %s", exc
            )
            return False

        if not record:
            return False

        chat_id = record.get("chat_id")
        if chat_id is None:
            return False

        try:
            self.clan_chat_id = int(chat_id)
        except (TypeError, ValueError):
            self.logger.warning(
                "Chat ID non valido nel contesto missione salvato: %s", chat_id
            )
            return False

        thread_id = record.get("message_thread_id")
        if thread_id is None:
            self.clan_topic_id = None
        else:
            try:
                self.clan_topic_id = int(thread_id)
            except (TypeError, ValueError):
                self.logger.debug(
                    "Topic ID non valido nel contesto missione salvato: %s",
                    thread_id,
                )
                self.clan_topic_id = None

        return True

    async def process_active_mission_auto(self) -> None:
        """Resolve the currently active mission, apply costs and store history."""

        url = f"https://api.wolvesville.com/clans/{self.clan_id}/quests/active"
        headers = {
            "Authorization": f"Bot {self.wolvesville_api_key}",
            "Accept": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    self.logger.error(
                        "Errore nel recupero della missione attiva: %s", resp.status
                    )
                    return
                active_data = await resp.json()

        quest = active_data.get("quest")
        if not quest:
            self.logger.info("Nessuna missione attiva trovata.")
            return

        mission_id = quest.get("id")
        tier_start_time = active_data.get("tierStartTime")
        if not mission_id or not tier_start_time:
            self.logger.error("Missione attiva priva di id o tierStartTime.")
            return

        if await self.db_manager.has_processed_active_mission(mission_id):
            self.logger.info(
                "Missione %s già processata. Nessuna operazione eseguita.", mission_id
            )
            return

        participants = active_data.get("participants", [])
        raw_usernames = [p.get("username") for p in participants if p.get("username")]
        if not raw_usernames:
            self.logger.info("Nessun partecipante trovato nella missione attiva.")
            return

        resolved_identities: List[Dict[str, Any]] = []
        alias_resolved_count = 0
        unresolved_usernames: List[str] = []
        for username in raw_usernames:
            identity = await self.identity_service.resolve_member_identity(username)
            resolved_username = identity.get("resolved_username")
            if not resolved_username:
                self.logger.warning(
                    "Missione attiva %s: ignorato username non valido (%s)",
                    mission_id,
                    username,
                )
                unresolved_usernames.append(username)
                continue
            if (
                identity.get("match") == "history"
                and identity.get("original_username")
                and identity.get("original_username") != resolved_username
            ):
                alias_resolved_count += 1
                self.logger.info(
                    "Missione attiva %s: alias risolto %s → %s",
                    mission_id,
                    identity.get("original_username"),
                    resolved_username,
                )
            resolved_identities.append(identity)

        if not resolved_identities:
            self.logger.info(
                "Missione %s: nessun partecipante valido dopo la risoluzione.",
                mission_id,
            )
            return

        participant_count = len(resolved_identities)

        mission_type = "Gem" if quest.get("purchasableWithGems", False) else "Gold"
        if mission_type == "Gold":
            cost = 500
        else:
            if participant_count > 7:
                cost = 140
            elif 5 <= participant_count <= 7:
                cost = 150
            else:
                cost = 0

        event_timestamp = None
        for candidate in (
            quest.get("lastCompletedAt"),
            active_data.get("lastCompletedAt"),
            quest.get("completedAt"),
            tier_start_time,
        ):
            event_timestamp = self.maintenance_service._parse_record_timestamp(candidate)
            if event_timestamp:
                break
        if event_timestamp is None:
            event_timestamp = datetime.now(timezone.utc)

        if cost:
            for identity in resolved_identities:
                await self.maintenance_service.update_user_balance(
                    identity["resolved_username"], mission_type, -cost
                )
                log_name = identity.get("resolved_username")
                original = identity.get("original_username")
                if original and original != log_name:
                    self.logger.info(
                        "Dedotto %s %s per %s (alias %s) nella missione %s",
                        cost,
                        "Oro" if mission_type == "Gold" else "Gem",
                        log_name,
                        original,
                        mission_id,
                    )
                else:
                    self.logger.info(
                        "Dedotto %s %s per %s nella missione %s",
                        cost,
                        "Oro" if mission_type == "Gold" else "Gem",
                        log_name,
                        mission_id,
                    )
        else:
            self.logger.info(
                "Missione attiva %s registrata senza costi aggiuntivi.",
                mission_id,
            )

        metadata = {
            "tier_start_time": tier_start_time,
            "participants_count": len(raw_usernames),
            "resolved_participants_count": participant_count,
            "alias_resolutions": alias_resolved_count,
            "linked_participants": sum(
                1 for identity in resolved_identities if identity.get("telegram_id")
            ),
            "cost_applied": cost,
        }
        if unresolved_usernames:
            metadata["unresolved_participants"] = unresolved_usernames
            metadata["unresolved_participants_count"] = len(unresolved_usernames)
        else:
            metadata["unresolved_participants_count"] = 0

        participant_entries: List[Dict[str, Any]] = []
        for identity in resolved_identities:
            entry: Dict[str, Any] = {
                "username": identity.get("resolved_username"),
                "original_username": identity.get("original_username"),
            }
            if identity.get("telegram_id") is not None:
                entry["telegram_id"] = identity.get("telegram_id")
            if identity.get("telegram_username"):
                entry["telegram_username"] = identity.get("telegram_username")
            if identity.get("match"):
                entry["match"] = identity.get("match")
            if identity.get("profile_snapshot"):
                entry["profile_snapshot"] = identity.get("profile_snapshot")
            participant_entries.append(entry)

        event_id = await self.db_manager.log_mission_participation(
            mission_id,
            mission_type,
            participant_entries,
            raw_usernames,
            cost_per_participant=cost,
            outcome="auto_processed",
            source="active_mission",
            occurred_at=event_timestamp,
            metadata=metadata,
        )

        await self.db_manager.mark_active_mission_processed(
            mission_id, tier_start_time, processed_at=event_timestamp
        )
        self.logger.info(
            "Missione %s processata e registrata (event_id=%s).",
            mission_id,
            event_id or "N/A",
        )

        await self._reward_mission_participants(
            resolved_identities,
            mission_type=mission_type,
            mission_id=mission_id,
            source="active_mission",
            outcome="auto_processed",
            metadata=metadata,
            event_id=event_id,
        )

    async def send_weekly_mission_skin(self) -> None:
        """Announce weekly missions and post the available skins."""

        if self.clan_chat_id is None:
            await self._ensure_clan_context()

        if self.clan_chat_id is None:
            self.logger.warning(
                "CHAT_ID non configurato, impossibile inviare l'annuncio settimanale."
            )
            return

        url = f"https://api.wolvesville.com/clans/{self.clan_id}/quests/available"
        announcement_message = (
            "🌞 Buongiorno Ragazzi e Ragazze!\n\n"
            "Qui il bot ad avvisarvi che oggi è **Lunedì**!!\n\n"
            "Giornata peggiore, ma per fortuna ci sono nuove missioni.\n"
            "Quindi andate a **votare**! 🗳️🔥"
        )

        try:
            await self.bot.send_message(
                chat_id=self.clan_chat_id,
                text=announcement_message,
                message_thread_id=self.clan_topic_id,
            )

            url_announcement = (
                f"https://api.wolvesville.com/clans/{self.clan_id}/announcements"
            )
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {self.wolvesville_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                payload = {"message": announcement_message}
                async with session.post(
                    url_announcement, headers=headers, json=payload
                ) as resp:
                    if resp.status in [200, 201, 204]:
                        self.logger.info("Annuncio inviato con successo nel gioco!")
                    else:
                        response_text = await resp.text()
                        self.logger.error(
                            "Errore nell'invio dell'annuncio: %s (Codice: %s)",
                            response_text,
                            resp.status,
                        )

            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {self.wolvesville_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                resp = await session.get(url, headers=headers)
                if resp.status != 200:
                    self.logger.error(
                        "Errore nel recupero delle skin programmate (status %s)",
                        resp.status,
                    )
                    return
                data = await resp.json()
                if not data:
                    self.logger.info(
                        "Nessuna skin disponibile per l'invio automatico"
                    )
                    return

                for quest in data:
                    promo_url = quest.get("promoImageUrl", "")
                    is_gem = quest.get("purchasableWithGems", False)
                    name = "Sconosciuto"
                    if promo_url:
                        filename = promo_url.split("/")[-1]
                        name = filename.split(".")[0]
                    tipo_str = "Gem" if is_gem else "Gold"
                    caption = f"Nome: {name}\nTipo: {tipo_str}"

                    if not promo_url:
                        continue

                    try:
                        async with session.get(promo_url) as r_img:
                            if r_img.status == 200:
                                raw = await r_img.read()
                                skin_file = types.BufferedInputFile(
                                    raw, filename="skin.png"
                                )
                                await self.bot.send_photo(
                                    chat_id=self.clan_chat_id,
                                    photo=skin_file,
                                    caption=caption,
                                    message_thread_id=self.clan_topic_id,
                                )
                    except Exception as exc:  # pragma: no cover - solo logging
                        self.logger.warning(
                            "Impossibile inviare %s: %s", promo_url, exc
                        )
        except Exception as exc:  # pragma: no cover - solo logging
            self.logger.error(
                "Errore nell'invio automatico delle skin: %s", exc
            )

    # ------------------------------------------------------------------
    # Helpers used by the /partecipanti FSM flow
    # ------------------------------------------------------------------
    async def get_available_missions(self) -> List[Dict[str, Any]]:
        url = f"https://api.wolvesville.com/clans/{self.clan_id}/quests/available"
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bot {self.wolvesville_api_key}",
                "Accept": "application/json",
            }
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                self.logger.error(
                    "Errore nel recupero delle missioni: status %s",
                    resp.status,
                )
                return []

    async def get_clan_member_ids(
        self, session: Optional[aiohttp.ClientSession] = None
    ) -> List[str]:
        close_session = False
        members: List[Dict[str, Any]] = []

        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        try:
            url = f"https://api.wolvesville.com/clans/{self.clan_id}/members"
            headers = {
                "Authorization": f"Bot {self.wolvesville_api_key}",
                "Accept": "application/json",
            }
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    error_body = await resp.text()
                    self.logger.error(
                        "Errore nel recupero dei membri del clan: status %s, risposta %s",
                        resp.status,
                        error_body,
                    )
                    return []

                data = await resp.json()
                if isinstance(data, list):
                    members = data
                elif isinstance(data, dict):
                    members_value = data.get("members", [])
                    if isinstance(members_value, list):
                        members = members_value
                    else:
                        self.logger.error(
                            "Formato inatteso nella risposta dei membri del clan: %s",
                            data,
                        )
                        return []
                else:
                    self.logger.error(
                        "Formato inatteso nella risposta dei membri del clan: %s",
                        data,
                    )
                    return []
        except Exception as exc:  # pragma: no cover - solo logging
            self.logger.error(
                "Eccezione durante il recupero dei membri del clan: %s", exc
            )
            return []
        finally:
            if close_session:
                await session.close()

        member_ids: List[str] = []
        for member in members:
            if not isinstance(member, dict):
                continue

            member_id = (
                member.get("playerId")
                or member.get("id")
                or member.get("memberId")
                or member.get("userId")
            )

            if not member_id:
                player_data = member.get("player")
                if isinstance(player_data, dict):
                    member_id = (
                        player_data.get("playerId")
                        or player_data.get("id")
                        or player_data.get("userId")
                    )

            if member_id:
                member_ids.append(str(member_id))
            else:
                self.logger.warning(
                    "Impossibile determinare l'ID per il membro: %s", member
                )

        unique_member_ids = list(dict.fromkeys(member_ids))
        if not unique_member_ids:
            self.logger.warning(
                "Nessun ID valido trovato nella lista dei membri del clan."
            )

        return unique_member_ids

    async def partecipanti_command(
        self, message: types.Message, state: FSMContext
    ) -> None:
        user_id = message.from_user.id if message.from_user else None
        if not self._is_mission_enable_allowed(message.chat, user_id):
            await self._handle_mission_enable_denied(
                message=message, callback=None, user_id=user_id
            )
            await state.clear()
            return

        await self.capture_clan_context(message)

        missions = await self.get_available_missions()
        if not missions:
            await message.answer("Nessuna missione disponibile al momento.")
            return

        buttons = []
        for mission in missions:
            promo_url = mission.get("promoImageUrl", "")
            name = "Sconosciuto"
            if promo_url:
                filename = promo_url.split("/")[-1]
                name = filename.split(".")[0]
            mission_id = mission.get("id")
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=name, callback_data=f"mission_select_{mission_id}"
                    )
                ]
            )
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(
            "Per quale missione si intende abilitare i partecipanti?",
            reply_markup=kb,
        )
        await state.update_data(available_missions=missions)
        await state.set_state(MissionStates.SELECTING_MISSION)

    async def mission_select_callback(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        chat = callback.message.chat if callback.message else None
        user_id = callback.from_user.id if callback.from_user else None
        if not self._is_mission_enable_allowed(chat, user_id):
            await self._handle_mission_enable_denied(
                message=callback.message, callback=callback, user_id=user_id
            )
            await state.clear()
            return

        selected_mission_id = callback.data.split("mission_select_")[-1]
        try:
            await callback.message.delete()
        except Exception as exc:  # pragma: no cover - solo logging
            self.logger.warning(
                "Errore nella cancellazione del messaggio: %s", exc
            )

        votes_url = f"https://api.wolvesville.com/clans/{self.clan_id}/quests/votes"
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bot {self.wolvesville_api_key}",
                "Accept": "application/json",
            }
            async with session.get(votes_url, headers=headers) as resp:
                if resp.status != 200:
                    await callback.message.answer("Impossibile recuperare i voti.")
                    return
                votes_data = await resp.json()

        votes_dict = votes_data.get("votes", {})
        mission_player_ids = votes_dict.get(selected_mission_id, [])
        self.logger.info(
            "Numero di voti per missione %s: %s",
            selected_mission_id,
            len(mission_player_ids),
        )
        await state.update_data(
            selected_mission_id=selected_mission_id,
            mission_player_ids=mission_player_ids,
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Si",
                        callback_data=f"enable_votes_yes_{selected_mission_id}",
                    ),
                    InlineKeyboardButton(
                        text="No",
                        callback_data=f"enable_votes_no_{selected_mission_id}",
                    ),
                ]
            ]
        )
        await callback.message.answer(
            "Vuoi abilitare i partecipanti che hanno votato per questa missione?",
            reply_markup=kb,
        )
        await state.set_state(MissionStates.CONFIRMING_PARTICIPANTS)

    def _build_mission_cost_summary(
        self,
        mission: Optional[Dict[str, Any]],
        participant_count: int,
    ) -> str:
        is_gem_mission = bool(mission and mission.get("purchasableWithGems", False))
        cost_per_participant = 0

        if is_gem_mission:
            if participant_count > 7:
                cost_per_participant = 140
            elif 5 <= participant_count <= 7:
                cost_per_participant = 150
            else:
                cost_per_participant = 0
        else:
            cost_per_participant = 500 if participant_count > 0 else 0

        if cost_per_participant == 0:
            return "💰 Costo missione: Nessun costo aggiuntivo previsto."

        total_cost = cost_per_participant * participant_count
        currency_total = "Gemme" if is_gem_mission else "Oro"
        currency_per = "gemme" if is_gem_mission else "oro"
        participant_label = "partecipante" if participant_count == 1 else "partecipanti"

        return (
            "💰 Costo missione: "
            f"{total_cost} {currency_total} totali "
            f"({cost_per_participant} {currency_per} per {participant_label})."
        )

    async def enable_votes_callback(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        chat = callback.message.chat if callback.message else None
        user_id = callback.from_user.id if callback.from_user else None
        if not self._is_mission_enable_allowed(chat, user_id):
            await self._handle_mission_enable_denied(
                message=callback.message, callback=callback, user_id=user_id
            )
            await state.clear()
            return

        parts = callback.data.split("_")
        decision = parts[2]
        try:
            await callback.message.delete()
        except Exception as exc:  # pragma: no cover - solo logging
            self.logger.warning(
                "Errore nella cancellazione del messaggio: %s", exc
            )

        if decision == "yes":
            data = await state.get_data()
            mission_player_ids_raw = data.get("mission_player_ids", [])
            selected_mission_id = data.get("selected_mission_id")
            available_missions = data.get("available_missions", [])

            mission_info: Optional[Dict[str, Any]] = None
            if isinstance(available_missions, list):
                for mission in available_missions:
                    if not isinstance(mission, dict):
                        continue
                    mission_id_candidate = str(mission.get("id"))
                    if mission_id_candidate == selected_mission_id:
                        mission_info = mission
                        break

            mission_player_ids = [str(pid) for pid in mission_player_ids_raw]
            mission_player_ids = list(dict.fromkeys(mission_player_ids))
            participant_count = len(mission_player_ids)

            self.logger.info(
                "Missione %s: abilito %s partecipanti dal voto",
                selected_mission_id,
                participant_count,
            )

            if not mission_player_ids:
                await callback.message.answer(
                    "Nessun partecipante da abilitare per questa missione."
                )
                await state.clear()
                return

            if not selected_mission_id:
                await callback.message.answer(
                    "Sessione non valida, ripeti /partecipanti."
                )
                await state.clear()
                return

            disable_failures: List[str] = []
            enable_failures: List[str] = []
            warning_messages: List[str] = []

            try:
                async with aiohttp.ClientSession() as session:
                    json_headers = {
                        "Authorization": f"Bot {self.wolvesville_api_key}",
                        "Content-Type": "application/json",
                    }

                    all_member_ids = await self.get_clan_member_ids(session)
                    if all_member_ids:
                        disable_payload = {"participateInQuests": False}
                        for member_id in all_member_ids:
                            url_put_disable = (
                                f"https://api.wolvesville.com/clans/{self.clan_id}/members/{member_id}/participateInQuests"
                            )
                            async with session.put(
                                url_put_disable,
                                headers=json_headers,
                                json=disable_payload,
                            ) as resp:
                                response_text = await resp.text()
                                self.logger.info(
                                    "PUT %s -> %s, %s",
                                    url_put_disable,
                                    resp.status,
                                    response_text,
                                )
                                if resp.status not in [200, 201, 204]:
                                    disable_failures.append(str(member_id))
                                    self.logger.error(
                                        "Errore nella disattivazione del membro %s: status %s, risposta %s",
                                        member_id,
                                        resp.status,
                                        response_text,
                                    )
                    else:
                        warning_messages.append(
                            "⚠️ Impossibile recuperare la lista completa dei membri, salto la disattivazione preventiva."
                        )
                        self.logger.warning(
                            "Lista membri vuota durante la disattivazione preventiva dei partecipanti alla missione."
                        )

                    enable_payload = {"participateInQuests": True}
                    for pid in mission_player_ids:
                        url_put_enable = (
                            f"https://api.wolvesville.com/clans/{self.clan_id}/members/{pid}/participateInQuests"
                        )
                        async with session.put(
                            url_put_enable,
                            headers=json_headers,
                            json=enable_payload,
                        ) as resp:
                            response_text = await resp.text()
                            self.logger.info(
                                "PUT %s -> %s, %s",
                                url_put_enable,
                                resp.status,
                                response_text,
                            )
                            if resp.status not in [200, 201, 204]:
                                enable_failures.append(str(pid))
                                self.logger.error(
                                    "Errore nell'abilitazione del membro %s: status %s, risposta %s",
                                    pid,
                                    resp.status,
                                    response_text,
                                )

                    await callback.message.answer(
                        "I partecipanti che hanno votato sono stati abilitati."
                    )

                    claim_url = (
                        f"https://api.wolvesville.com/clans/{self.clan_id}/quests/claim"
                    )
                    claim_headers = {
                        "Authorization": f"Bot {self.wolvesville_api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    }
                    claim_payload = {"questId": selected_mission_id}

                    async with session.post(
                        claim_url, headers=claim_headers, json=claim_payload
                    ) as resp:
                        claim_body = await resp.text()
                        if resp.status in [200, 201, 204]:
                            cost_summary = self._build_mission_cost_summary(
                                mission_info,
                                participant_count,
                            )
                            message_lines = [
                                "🚀 Missione avviata con successo.",
                                cost_summary,
                            ]
                            await callback.message.answer("\n".join(message_lines))
                            self.logger.info(
                                "Missione %s avviata con successo: %s",
                                selected_mission_id,
                                claim_body,
                            )
                            self.logger.info(
                                "Missione %s costo stimato: %s",
                                selected_mission_id,
                                cost_summary,
                            )
                        else:
                            self.logger.error(
                                "Errore nell'avvio della missione %s: status %s, risposta %s",
                                selected_mission_id,
                                resp.status,
                                claim_body,
                            )
                            await callback.message.answer(
                                f"⚠️ Impossibile avviare la missione (status {resp.status})."
                            )

                    for message_text in warning_messages:
                        await callback.message.answer(message_text)

                    if disable_failures:
                        await callback.message.answer(
                            f"⚠️ Disattivazione non riuscita per {len(disable_failures)} membri. Controlla i log per i dettagli."
                        )

                    if enable_failures:
                        await callback.message.answer(
                            f"⚠️ Abilitazione non riuscita per {len(enable_failures)} partecipanti. Controlla i log per i dettagli."
                        )
            except Exception as exc:  # pragma: no cover - solo logging
                self.logger.error(
                    "Errore durante la gestione dell'abilitazione missione per %s: %s",
                    selected_mission_id,
                    exc,
                )
                await callback.message.answer(
                    "Si è verificato un errore durante l'abilitazione dei partecipanti. Riprova più tardi."
                )
        else:
            await callback.message.answer("Abilitazione annullata.")

        await state.clear()

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------
    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.message.register(
            self.partecipanti_command, Command("partecipanti")
        )
        dispatcher.callback_query.register(
            self.mission_select_callback,
            StateFilter(MissionStates.SELECTING_MISSION),
            F.data.startswith("mission_select_"),
        )
        dispatcher.callback_query.register(
            self.enable_votes_callback,
            StateFilter(MissionStates.CONFIRMING_PARTICIPANTS),
            F.data.startswith("enable_votes_"),
        )

