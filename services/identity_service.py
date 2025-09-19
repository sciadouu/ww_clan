"""Servizio centralizzato per la gestione di profili e identità Telegram/Wolvesville."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

import aiohttp
from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNotFound

def format_telegram_username(username: Optional[str]) -> str:
    """Restituisce uno username Telegram formattato con @ oppure un segnaposto."""

    if not username:
        return "—"
    cleaned = username.strip()
    if not cleaned:
        return "—"
    return cleaned if cleaned.startswith("@") else f"@{cleaned}"


def format_markdown_code(value: Optional[Any]) -> str:
    """Formatta un valore come blocco inline oppure restituisce un segnaposto."""

    if value is None:
        return "—"
    text = str(value).strip()
    if not text or text == "—":
        return "—"
    safe = text.replace("`", "\\`")
    return f"`{safe}`"


def build_profile_snapshot(profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Estrae un sottoinsieme sicuro dei dati del profilo per logging e auditing."""

    if not profile:
        return None

    snapshot: Dict[str, Any] = {
        "telegram_id": profile.get("telegram_id"),
        "telegram_username": profile.get("telegram_username"),
        "game_username": profile.get("game_username"),
        "wolvesville_id": profile.get("wolvesville_id"),
    }

    if profile.get("updated_at"):
        snapshot["updated_at"] = profile.get("updated_at")
    if profile.get("created_at"):
        snapshot["created_at"] = profile.get("created_at")

    verification = profile.get("verification")
    if isinstance(verification, dict):
        snapshot["verification"] = {
            key: verification.get(key)
            for key in ("status", "verified_at", "method", "code")
            if verification.get(key) is not None
        }

    return snapshot


class IdentityService:
    """Gestisce sincronizzazione, risoluzione e notifiche relative ai profili utenti."""

    def __init__(
        self,
        *,
        bot,
        db_manager,
        wolvesville_api_key: str,
        schedule_admin_notification: Callable[..., None],
        member_list_refresh: Optional[Callable[[], Awaitable[None]]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._bot = bot
        self._db_manager = db_manager
        self._wolvesville_api_key = wolvesville_api_key
        self._schedule_admin_notification = schedule_admin_notification
        self._member_list_refresh = member_list_refresh
        self._logger = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Helper per notifiche e sincronizzazione
    # ------------------------------------------------------------------
    async def handle_telegram_sync_result(
        self, result: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Gestisce gli aggiornamenti dello username Telegram e ritorna il profilo."""

        if not result:
            return None

        profile = result.get("profile")
        if profile is None:
            return None

        if result.get("telegram_username_changed"):
            game_username = profile.get("game_username")
            if game_username:
                old_username = format_telegram_username(
                    result.get("previous_telegram_username")
                )
                new_username = format_telegram_username(profile.get("telegram_username"))
                self._logger.info(
                    "Username Telegram aggiornato per %s: %s → %s",
                    game_username,
                    old_username,
                    new_username,
                )
                await self._trigger_member_list_refresh()

        return profile

    async def handle_profile_link_result(
        self, result: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Analizza il linking del profilo e gestisce variazioni sugli username."""

        if not result or result.get("conflict"):
            return result.get("profile") if result else None

        profile = result.get("profile")
        if profile is None:
            return None

        refresh_needed = False

        if result.get("game_username_changed") and result.get("previous_game_username"):
            telegram_display = format_telegram_username(profile.get("telegram_username"))
            self._logger.info(
                "Username Wolvesville aggiornato: %s → %s (Telegram %s)",
                result.get("previous_game_username"),
                profile.get("game_username"),
                telegram_display,
            )
            refresh_needed = True

        if result.get("telegram_username_changed"):
            refresh_needed = True

        if result.get("created"):
            refresh_needed = True

        if refresh_needed:
            await self._trigger_member_list_refresh()

        return profile

    async def _trigger_member_list_refresh(self) -> None:
        """Richiede l'aggiornamento della lista membri se configurato."""

        if not self._member_list_refresh:
            return

        try:
            await self._member_list_refresh()
        except Exception as exc:  # pragma: no cover - log diagnostico
            self._logger.warning(
                "Aggiornamento lista membri non riuscito: %s",
                exc,
            )

    # ------------------------------------------------------------------
    # API pubbliche
    # ------------------------------------------------------------------
    async def resolve_member_identity(self, username: Optional[str]) -> Dict[str, Any]:
        """Risolvi uno username di gioco in base al profilo collegato."""

        raw_username = username or ""
        cleaned_username = raw_username.strip()

        identity: Dict[str, Any] = {
            "input_username": username,
            "original_username": cleaned_username or None,
            "resolved_username": cleaned_username or None,
            "telegram_id": None,
            "telegram_username": None,
            "match": None,
            "profile": None,
            "profile_snapshot": None,
        }

        if not cleaned_username:
            return identity

        try:
            resolution = await self._db_manager.resolve_profile_by_game_alias(
                cleaned_username
            )
        except Exception as exc:  # pragma: no cover - log diagnostico
            self._logger.warning(
                "Impossibile risolvere il profilo per %s: %s",
                cleaned_username,
                exc,
            )
            return identity

        if not resolution:
            return identity

        profile = resolution.get("profile") or {}
        resolved_username = resolution.get("resolved_username") or cleaned_username

        identity.update(
            {
                "resolved_username": resolved_username,
                "match": resolution.get("match"),
                "telegram_id": profile.get("telegram_id"),
                "telegram_username": profile.get("telegram_username"),
                "profile": profile,
                "profile_snapshot": build_profile_snapshot(profile),
            }
        )

        return identity

    async def ensure_telegram_profile_synced(
        self, user: Optional[types.User]
    ) -> None:
        """Allinea il profilo Telegram con il database e notifica eventuali cambi username."""

        if user is None:
            return

        full_name_parts = [user.first_name or "", user.last_name or ""]
        full_name = " ".join(part for part in full_name_parts if part).strip() or None

        try:
            result = await self._db_manager.sync_telegram_metadata(
                user.id,
                telegram_username=user.username,
                full_name=full_name,
            )
        except Exception as exc:  # pragma: no cover - logging di sicurezza
            self._logger.warning(
                "Impossibile aggiornare il profilo Telegram per %s: %s",
                getattr(user, "id", "?"),
                exc,
            )
            return

        if not result or result.get("created"):
            return

        await self.handle_telegram_sync_result(result)

    async def refresh_linked_profiles(self) -> None:
        """Sincronizza periodicamente gli username Telegram e Wolvesville già collegati."""

        try:
            profiles = await self._db_manager.list_linked_player_profiles()
        except Exception as exc:
            self._logger.warning("Impossibile recuperare i profili collegati: %s", exc)
            return

        if not profiles:
            return

        async with aiohttp.ClientSession() as wolvesville_session:
            for profile in profiles:
                telegram_id = profile.get("telegram_id")
                if not telegram_id:
                    continue

                latest_profile = profile

                try:
                    chat = await self._bot.get_chat(telegram_id)
                except TelegramForbiddenError:
                    self._logger.debug(
                        "Sync Telegram ignorato per %s: bot bloccato", telegram_id
                    )
                except TelegramNotFound:
                    self._logger.debug(
                        "Sync Telegram ignorato per %s: utente non trovato", telegram_id
                    )
                except TelegramBadRequest as exc:
                    self._logger.debug(
                        "Sync Telegram fallito per %s: %s", telegram_id, exc
                    )
                else:
                    chat_full_name = (
                        " ".join(
                            part
                            for part in [chat.first_name, chat.last_name]
                            if part
                        ).strip()
                        or None
                    )
                    try:
                        result = await self._db_manager.sync_telegram_metadata(
                            telegram_id,
                            telegram_username=chat.username,
                            full_name=chat_full_name,
                        )
                    except Exception as exc:  # pragma: no cover - diagnosi schedulatore
                        self._logger.warning(
                            "Sync Telegram fallito per %s: %s", telegram_id, exc
                        )
                    else:
                        updated_profile = await self.handle_telegram_sync_result(result)
                        if updated_profile:
                            latest_profile = updated_profile

                wolvesville_id = (
                    latest_profile.get("wolvesville_id")
                    if isinstance(latest_profile, dict)
                    else profile.get("wolvesville_id")
                )
                if not wolvesville_id:
                    continue

                player_info = await self.fetch_player_by_id(
                    wolvesville_id,
                    session=wolvesville_session,
                )
                if not player_info:
                    continue

                new_username = player_info.get("username")
                if not new_username:
                    continue

                try:
                    link_result = await self._db_manager.link_player_profile(
                        telegram_id,
                        game_username=new_username,
                        telegram_username=latest_profile.get("telegram_username")
                        if isinstance(latest_profile, dict)
                        else profile.get("telegram_username"),
                        full_name=latest_profile.get("full_name")
                        if isinstance(latest_profile, dict)
                        else profile.get("full_name"),
                        wolvesville_id=wolvesville_id,
                        verified=False,
                        verification_code=None,
                        verification_method=None,
                    )
                except Exception as exc:
                    self._logger.warning(
                        "Aggiornamento profilo Wolvesville fallito per %s: %s",
                        telegram_id,
                        exc,
                    )
                    continue

                if link_result and link_result.get("conflict"):
                    self._logger.warning(
                        "Conflitto durante l'aggiornamento del profilo per %s: %s",
                        telegram_id,
                        link_result.get("reason"),
                    )
                    continue

                updated_profile = await self.handle_profile_link_result(link_result)
                if updated_profile:
                    latest_profile = updated_profile

                await asyncio.sleep(0.1)

    async def fetch_player_by_id(
        self,
        player_id: str,
        *,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> Optional[Dict[str, Any]]:
        """Recupera un giocatore tramite ID Wolvesville."""

        if not player_id:
            return None

        url = f"https://api.wolvesville.com/players/{player_id}"
        headers = {
            "Authorization": f"Bot {self._wolvesville_api_key}",
            "Accept": "application/json",
        }

        async def _do_request(client: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
            async with client.get(url, headers=headers) as response:
                if response.status != 200:
                    self._logger.warning(
                        "Impossibile recuperare il giocatore con ID %s (status %s)",
                        player_id,
                        response.status,
                    )
                    return None
                return await response.json()

        try:
            if session is not None:
                return await _do_request(session)
            async with aiohttp.ClientSession() as owned_session:
                return await _do_request(owned_session)
        except Exception as exc:
            self._logger.error(
                "Errore durante il recupero del giocatore %s: %s", player_id, exc
            )
            return None

