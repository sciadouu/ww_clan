"""Mission management service for Wolvesville bot."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.db_manager import MongoManager
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

    # ---------------------------------------------------------------------
    # Public API used by other components (scheduler, commands, services)
    # ---------------------------------------------------------------------
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
            metadata=metadata_payload,
        )

        if event_id:
            self.logger.info(
                "Registrata partecipazione missione %s (event_id=%s) con %s partecipanti.",
                mission_id or "manual",
                event_id,
                participant_count,
            )

        return event_id

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
            metadata=metadata,
        )

        await self.db_manager.mark_active_mission_processed(mission_id, tier_start_time)
        self.logger.info(
            "Missione %s processata e registrata (event_id=%s).",
            mission_id,
            event_id or "N/A",
        )

    async def send_weekly_mission_skin(self) -> None:
        """Announce weekly missions and post the available skins."""

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

    async def enable_votes_callback(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
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

            mission_player_ids = [str(pid) for pid in mission_player_ids_raw]
            mission_player_ids = list(dict.fromkeys(mission_player_ids))

            self.logger.info(
                "Missione %s: abilito %s partecipanti dal voto",
                selected_mission_id,
                len(mission_player_ids),
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
                            await callback.message.answer(
                                "🚀 Missione avviata con successo."
                            )
                            self.logger.info(
                                "Missione %s avviata con successo: %s",
                                selected_mission_id,
                                claim_body,
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

