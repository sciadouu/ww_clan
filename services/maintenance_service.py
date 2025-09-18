"""Servizio per la manutenzione del database e la gestione del ledger."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence

import aiohttp

from services.identity_service import IdentityService


class MaintenanceService:
    """Accorpa housekeeping del database e gestione del ledger."""

    def __init__(
        self,
        *,
        bot,
        db_manager,
        identity_service: IdentityService,
        clan_id: str,
        wolvesville_api_key: str,
        admin_ids: Sequence[int],
        logger: logging.Logger | None = None,
    ) -> None:
        self._bot = bot
        self._db_manager = db_manager
        self._identity_service = identity_service
        self._clan_id = clan_id
        self._wolvesville_api_key = wolvesville_api_key
        self._admin_ids = tuple(admin_ids)
        self._logger = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Operazioni di housekeeping
    # ------------------------------------------------------------------
    async def clean_duplicate_users(self) -> None:
        """Rimuove utenti duplicati dal database mantenendo log di riepilogo."""

        try:
            removed_info = await self._db_manager.remove_duplicate_users()
        except Exception as exc:
            self._logger.error("Errore durante pulizia duplicati: %s", exc)
            return

        total_removed = sum(item.get("removed", 0) for item in removed_info)
        for item in removed_info:
            username = item.get("username", "sconosciuto")
            removed = item.get("removed", 0)
            removed_ids = item.get("removed_ids", [])
            self._logger.info(
                "Eliminati %s duplicati per %s (documenti rimossi: %s)",
                removed,
                username,
                removed_ids,
            )

        self._logger.info(
            "Pulizia duplicati completata. Eliminati %s duplicati.", total_removed
        )

    async def check_clan_departures(self) -> None:
        """Controlla membri usciti dal clan e gestisce debiti/pulizia."""

        try:
            url = f"https://api.wolvesville.com/clans/{self._clan_id}/members"
            headers = {
                "Authorization": f"Bot {self._wolvesville_api_key}",
                "Accept": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        self._logger.error(
                            "Errore nel recupero membri clan: %s", response.status
                        )
                        return
                    current_members = await response.json()
        except Exception as exc:
            self._logger.error(
                "Errore durante recupero membri clan per controllo uscite: %s", exc
            )
            return

        current_usernames = {
            member.get("username")
            for member in current_members
            if isinstance(member, dict)
            and isinstance(member.get("username"), str)
            and member.get("username")
        }

        try:
            db_users = await self._db_manager.list_users()
        except Exception as exc:
            self._logger.error("Errore nel recupero utenti da MongoDB: %s", exc)
            return

        users_removed = 0
        debt_notifications = 0

        for user in db_users:
            username = user.get("username")
            if not username or username in current_usernames:
                continue

            donazioni = user.get("donazioni", {})
            oro = donazioni.get("Oro", 0)
            gem = donazioni.get("Gem", 0)

            try:
                oro = int(oro) if oro is not None else 0
                gem = int(gem) if gem is not None else 0
            except (ValueError, TypeError):
                oro = gem = 0

            if oro < 0 or gem < 0:
                debt_message = (
                    f"🚨 <b>USCITA CON DEBITI</b> 🚨\n\n"
                    f"👤 <b>Utente:</b> {username}\n"
                    f"💰 <b>Debito Oro:</b> {abs(oro) if oro < 0 else 0:,}\n"
                    f"💎 <b>Debito Gem:</b> {abs(gem) if gem < 0 else 0:,}\n\n"
                    f"⚠️ L'utente ha abbandonato il clan con debiti non saldati!\n"
                    f"📅 Data controllo: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                )

                for admin_id in self._admin_ids:
                    try:
                        await self._bot.send_message(
                            admin_id, debt_message, parse_mode="HTML"
                        )
                        debt_notifications += 1
                    except Exception as exc:
                        self._logger.warning(
                            "Impossibile inviare notifica debito ad admin %s: %s",
                            admin_id,
                            exc,
                        )

                self._logger.info(
                    "Notificato debito per utente uscito: %s (Oro: %s, Gem: %s)",
                    username,
                    oro,
                    gem,
                )
                continue

            try:
                removed = await self._db_manager.remove_user_by_username(username)
            except Exception as exc:
                self._logger.warning(
                    "Impossibile rimuovere l'utente %s dal database: %s",
                    username,
                    exc,
                )
                continue

            if removed > 0:
                users_removed += 1
                self._logger.info(
                    "Utente %s rimosso dal database (nessun debito)", username
                )

        self._logger.info(
            "Controllo uscite clan completato: %s utenti rimossi, %s notifiche debiti inviate",
            users_removed,
            debt_notifications,
        )

    async def prepopulate_users(self) -> None:
        """Pre-popolazione utenti dal clan con controllo duplicati."""

        url = f"https://api.wolvesville.com/clans/{self._clan_id}/members"
        headers = {
            "Authorization": f"Bot {self._wolvesville_api_key}",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    self._logger.error(
                        "Errore nel recupero dei membri: %s", response.status
                    )
                    return
                members = await response.json()

        for member in members:
            username = None
            if isinstance(member, dict):
                username = member.get("username")
            if username:
                try:
                    inserted = await self._db_manager.ensure_user(username)
                except Exception as exc:
                    self._logger.warning(
                        "Impossibile pre-popolare l'utente %s: %s", username, exc
                    )
                    continue
                if inserted:
                    self._logger.info(
                        "Utente %s pre-popolato con bilancio 0.", username
                    )

    # ------------------------------------------------------------------
    # Gestione ledger
    # ------------------------------------------------------------------
    async def update_user_balance(
        self, username: str, currency: str, amount: int
    ) -> str:
        """Aggiorna il bilancio dell'utente e restituisce la valuta normalizzata."""

        normalized_currency = await self._db_manager.update_user_balance(
            username, currency, amount
        )
        self._logger.info(
            "Aggiornato bilancio per %s: %s %+d",
            username,
            normalized_currency,
            amount,
        )
        return normalized_currency

    async def process_ledger(self) -> None:
        """Recupera il ledger e aggiorna il DB con i record DONATE non processati."""

        url = f"https://api.wolvesville.com/clans/{self._clan_id}/ledger"
        headers = {
            "Authorization": f"Bot {self._wolvesville_api_key}",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    self._logger.error(
                        "Errore nel recupero del ledger: %s", response.status
                    )
                    return
                ledger_data = await response.json()

        for record in ledger_data:
            record_id = record.get("id")
            record_type = record.get("type", "")
            if record_type != "DONATE":
                continue

            if await self._db_manager.has_processed_ledger(record_id):
                continue

            username = record.get("playerUsername")
            gold_amount = record.get("gold", 0) or 0
            gems_amount = record.get("gems", 0) or 0
            occurred_at = self._parse_record_timestamp(
                record.get("createdAt")
                or record.get("created_at")
                or record.get("timestamp")
                or record.get("time")
            )

            if username and (gold_amount > 0 or gems_amount > 0):
                identity = await self._identity_service.resolve_member_identity(username)
                resolved_username = identity.get("resolved_username")
                if not resolved_username:
                    self._logger.warning(
                        "Record ledger %s ignorato: username non valido (%s)",
                        record_id,
                        username,
                    )
                    await self._db_manager.mark_ledger_processed(
                        record_id, raw_record=record
                    )
                    continue

                original_username = identity.get("original_username") or username
                if (
                    identity.get("match") == "history"
                    and original_username
                    and original_username != resolved_username
                ):
                    self._logger.info(
                        "Ledger: risolto alias %s → %s (record %s)",
                        original_username,
                        resolved_username,
                        record_id,
                    )

                if gold_amount > 0:
                    await self.update_user_balance(
                        resolved_username, "Oro", gold_amount
                    )
                if gems_amount > 0:
                    await self.update_user_balance(
                        resolved_username, "Gem", gems_amount
                    )

                await self._db_manager.log_donation(
                    record_id,
                    resolved_username,
                    gold_amount,
                    gems_amount,
                    raw_record=record,
                    processed_at=occurred_at,
                    telegram_id=identity.get("telegram_id"),
                    telegram_username=identity.get("telegram_username"),
                    profile_snapshot=identity.get("profile_snapshot"),
                    original_username=original_username,
                    match_source=identity.get("match"),
                )

            await self._db_manager.mark_ledger_processed(
                record_id, raw_record=record, processed_at=occurred_at
            )

    @staticmethod
    def _parse_record_timestamp(value) -> datetime | None:
        """Prova a convertire valori eterogenei in datetime timezone-aware."""

        if not value:
            return None

        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            except (TypeError, ValueError, OSError):  # pragma: no cover - input non atteso
                return None

        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

        return None

