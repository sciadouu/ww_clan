"""Mission related callbacks and helpers."""

from __future__ import annotations

import os
from typing import Iterable, Optional, Set

import aiohttp
from aiogram import Router, types
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup


class MissionCallback(CallbackData, prefix="mission"):
    """Callback payload used for the mission inline keyboard."""

    action: str


class MissionHandlers:
    """Expose mission UI flows separate from business logic services."""

    def __init__(
        self,
        *,
        clan_id: str,
        wolvesville_api_key: str,
        skip_image_path: str,
        logger,
        authorized_group_ids: Optional[Iterable[int]] = None,
        owner_id: Optional[int] = None,
    ) -> None:
        self.clan_id = clan_id
        self.wolvesville_api_key = wolvesville_api_key
        self.skip_image_path = skip_image_path
        self.logger = logger
        self.authorized_group_ids: Set[int] = {
            int(chat_id) for chat_id in authorized_group_ids or []
        }
        self.owner_id = owner_id

        self.router = Router()
        self.router.callback_query.register(
            self.handle_mission_callback, MissionCallback.filter()
        )

    async def start_flow(self, message: types.Message, state: FSMContext) -> None:
        """Present the mission menu with skip/skin options."""

        from_user = message.from_user.id if message.from_user else None
        show_skip = self._is_skip_allowed(message.chat, from_user)

        row = [
            InlineKeyboardButton(
                text="Skin",
                callback_data=MissionCallback(action="skin").pack(),
            )
        ]
        if show_skip:
            row.append(
                InlineKeyboardButton(
                    text="Skip",
                    callback_data=MissionCallback(action="skip").pack(),
                )
            )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[row])
        await message.answer("Missioni", reply_markup=keyboard)

    async def handle_mission_callback(
        self,
        callback: types.CallbackQuery,
        callback_data: MissionCallback,
        state: FSMContext,
    ) -> None:
        """React to mission actions (skip timer or list skins)."""

        try:
            await callback.message.delete()
        except Exception:  # pragma: no cover - le failure non devono bloccare il flow
            pass

        if callback_data.action == "skip":
            await self._handle_skip(callback)
        else:
            await self._handle_skins(callback)

    def _is_skip_allowed(
        self,
        chat: Optional[types.Chat],
        user_id: Optional[int],
    ) -> bool:
        if chat is None:
            return False

        chat_type = getattr(chat, "type", "")
        chat_id = getattr(chat, "id", None)

        if chat_type == "private":
            return user_id is not None and user_id == self.owner_id

        if chat_id is None:
            return False

        if self.authorized_group_ids:
            return chat_id in self.authorized_group_ids

        return chat_type in {"group", "supergroup"}

    async def _handle_skip(self, callback: types.CallbackQuery) -> None:
        url = (
            f"https://api.wolvesville.com/clans/{self.clan_id}/quests/active/skipWaitingTime"
        )

        user_id = callback.from_user.id
        username = callback.from_user.username or callback.from_user.first_name

        if not self._is_skip_allowed(callback.message.chat, user_id):
            warning_text = (
                "❌ Il comando /skip missione può essere utilizzato solo nel gruppo del clan."
                " In privato è consentito esclusivamente all'owner."
            )
            if self.owner_id is not None and user_id != self.owner_id:
                chat_id = callback.message.chat.id if callback.message else "unknown"
                self.logger.warning(
                    "Skip command denied for user %s in chat %s", user_id, chat_id
                )
            await callback.answer("Accesso negato", show_alert=True)
            await callback.message.answer(warning_text)
            return

        self.logger.info(
            "Skip command requested by user %s (%s)",
            user_id,
            username,
        )

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {self.wolvesville_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }

                self.logger.info("Making skip API call to: %s", url)

                async with session.post(url, headers=headers) as resp:
                    response_text = await resp.text()
                    self.logger.info(
                        "Skip API response: Status %s, Response: %s",
                        resp.status,
                        response_text,
                    )

                    if resp.status == 200:
                        await self._acknowledge_skip_success(callback)
                    elif resp.status == 400:
                        await callback.message.answer(
                            "❌ **Impossibile saltare il tempo**\n\n"
                            "• Nessuna missione attiva\n"
                            "• Missione già completata\n"
                            "• Tempo di attesa già scaduto"
                        )
                        self.logger.warning(
                            "Skip failed - No active mission (400): %s",
                            response_text,
                        )
                    elif resp.status == 401:
                        await callback.message.answer(
                            "❌ **Errore di autorizzazione**\n\n"
                            "Il bot non ha i permessi per saltare il tempo."
                        )
                        self.logger.error(
                            "Skip failed - Unauthorized (401): %s",
                            response_text,
                        )
                    elif resp.status == 404:
                        await callback.message.answer(
                            "❌ **Clan o missione non trovati**\n\n"
                            "Verifica la configurazione del clan."
                        )
                        self.logger.error(
                            "Skip failed - Not found (404): %s",
                            response_text,
                        )
                    else:
                        await callback.message.answer(
                            "❌ **Errore durante lo skip**\n\n"
                            f"Codice: {resp.status}\n"
                            "Riprova più tardi."
                        )
                        self.logger.error(
                            "Skip failed - HTTP %s: %s",
                            resp.status,
                            response_text,
                        )
        except aiohttp.ClientError as network_error:
            self.logger.error("Network error during skip: %s", network_error)
            await callback.message.answer(
                "❌ **Errore di connessione**\n\n"
                "Impossibile contattare il server. Riprova più tardi."
            )
        except Exception as exc:  # pragma: no cover - logging difensivo
            self.logger.error("Unexpected error during skip: %s", exc)
            await callback.message.answer(
                "❌ **Errore imprevisto durante lo skip**\n\n"
                "Controlla i log o riprova più tardi."
            )

    async def _acknowledge_skip_success(self, callback: types.CallbackQuery) -> None:
        try:
            if os.path.exists(self.skip_image_path):
                photo = FSInputFile(self.skip_image_path)
                await callback.message.answer_photo(
                    photo=photo,
                    caption=(
                        "⏰ **Tempo saltato con successo!** 🚀\n\n"
                        "Tornare a farmare piccoli vermi!! 🐛\n\n"
                        "_La missione è stata completata automaticamente._"
                    ),
                )
                self.logger.info(
                    "Skip successful - Image sent: %s", self.skip_image_path
                )
            else:
                await callback.message.answer(
                    "⏰ **Tempo saltato con successo!** 🚀\n\n"
                    "Tornare a farmare piccoli vermi!! 🐛"
                )
                self.logger.warning(
                    "Skip image not found: %s", self.skip_image_path
                )
        except Exception as img_error:  # pragma: no cover - fallback al testo
            self.logger.error("Error sending skip image: %s", img_error)
            await callback.message.answer(
                "⏰ **Tempo saltato con successo!** 🚀\n\n"
                "Tornare a farmare piccoli vermi!! 🐛\n"
                "_(Immagine non disponibile)_"
            )

    async def _handle_skins(self, callback: types.CallbackQuery) -> None:
        url = f"https://api.wolvesville.com/clans/{self.clan_id}/quests/available"
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {self.wolvesville_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                resp = await session.get(url, headers=headers)
                if resp.status != 200:
                    await callback.message.answer("Impossibile recuperare le skin!")
                    return
                data = await resp.json()
                if not data:
                    await callback.message.answer("Nessuna skin disponibile!")
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
                        await callback.message.answer(caption)
                        continue
                    try:
                        async with session.get(promo_url) as image_response:
                            if image_response.status != 200:
                                raise RuntimeError(
                                    f"Status {image_response.status} while fetching {promo_url}"
                                )
                            raw = await image_response.read()
                            input_file = types.BufferedInputFile(
                                raw, filename="skin.png"
                            )
                            await callback.message.answer_photo(
                                photo=input_file,
                                caption=caption,
                            )
                    except Exception as exc:
                        self.logger.warning(
                            "Impossibile inviare %s: %s", promo_url, exc
                        )
        except Exception as exc:
            self.logger.error("Errore missione Skin: %s", exc)
            await callback.message.answer("Impossibile recuperare le skin!")
