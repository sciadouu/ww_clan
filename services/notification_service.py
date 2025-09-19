# notification_service.py - VERSIONE CORRETTA

import asyncio
import logging
from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict
from aiogram import Bot

class NotificationType(Enum):
    """Tipi di notifica con emoji associati"""
    CRITICAL = "🚨"
    WARNING = "⚠️"
    INFO = "ℹ️"
    SUCCESS = "✅"
    ERROR = "❌"
    SECURITY = "🔒"

class EnhancedNotificationService:
    """
    Servizio di notifiche completo e corretto.

    CORREZIONI IMPLEMENTATE:
    - send_startup_notification() ora invia SEMPRE al proprietario
    - send_debt_notification() ora invia al canale admin E ai singoli admin
    - Timestamp corretto per CEST
    - Sistema blacklist funzionante
    """

    def __init__(self, bot: Bot, admin_ids: List[int], admin_channel_id: Optional[int] = None, owner_id: Optional[int] = None):
        self.bot = bot
        self.admin_ids = admin_ids
        self.admin_channel_id = admin_channel_id
        self.owner_id = owner_id
        self.logger = logging.getLogger(__name__)

        # Rate limiting e tracking blacklist
        self.last_notification_time = {}
        self.min_interval = 5
        self.group_blacklist = {}
        self.max_attempts = 3
        self.duplicate_interval_seconds = 3

    def get_local_timestamp(self) -> str:
        """
        Corregge il problema del timestamp con 2 ore di ritardo.
        Converte UTC in CEST (UTC+2).
        """
        utc_now = datetime.now(timezone.utc)
        local_offset = timedelta(hours=2)  # CEST offset
        local_time = utc_now + local_offset
        return local_time.strftime('%d/%m/%Y %H:%M:%S CEST')

    # ================================================================================
    # METODI CORRETTI - Risolvono i problemi principali
    # ================================================================================

    async def send_startup_notification(self):
        """
        CORREZIONE PRINCIPALE: Invia notifica di avvio sia al proprietario che al canale admin.
        PRIMA: inviava solo nel gruppo admin
        ORA: invia a entrambe le destinazioni
        """
        message = (
            f"🤖 **BOT AVVIATO**\n\n"
            f"✅ **Status:** Bot attivo e operativo\n"
            f"📅 **Timestamp:** {self.get_local_timestamp()}\n\n"
            f"🔧 **Funzionalità attive:**\n"
            f"• Monitoraggio clan\n"
            f"• Gestione bilanci\n"
            f"• Sistema notifiche\n"
            f"• Controllo gruppi autorizzati\n"
            f"• Sistema blacklist gruppi"
        )

        # CORREZIONE PRINCIPALE: Invia SEMPRE al proprietario
        if self.owner_id:
            try:
                await self.bot.send_message(
                    chat_id=self.owner_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                self.logger.info(f"✅ Notifica startup inviata al proprietario {self.owner_id}")
            except Exception as e:
                self.logger.error(f"❌ Errore invio notifica startup al proprietario: {e}")

        # Invia anche al canale admin
        if self.admin_channel_id:
            try:
                await self.bot.send_message(
                    chat_id=self.admin_channel_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                self.logger.info("✅ Notifica startup inviata al canale admin")
            except Exception as e:
                self.logger.error(f"❌ Errore invio notifica startup al canale admin: {e}")

    async def send_debt_notification(self, user_data: Dict, debt_info: Dict):
        """
        CORREZIONE PRINCIPALE: Invia notifica debiti sia agli ADMIN_IDS che al canale admin.
        PRIMA: notifiche debiti inviate solo al proprietario
        ORA: invia a canale admin + tutti gli admin + proprietario (se diverso dagli admin)
        """
        message = (
            f"💸 **UTENTE CON DEBITI USCITO DAL CLAN**\n\n"
            f"👤 **Utente:** {user_data.get('username', 'Sconosciuto')}\n"
            f"🆔 **User ID:** `{user_data.get('user_id', 'N/A')}`\n\n"
            f"💰 **Debiti:**\n"
            f"🏆 **Oro:** {debt_info.get('oro', 0):,}\n"
            f"💎 **Gem:** {debt_info.get('gem', 0):,}\n\n"
            f"📅 **Timestamp:** {self.get_local_timestamp()}"
        )

        # CORREZIONE PRINCIPALE: Invia al canale admin
        if self.admin_channel_id:
            try:
                await self.bot.send_message(
                    chat_id=self.admin_channel_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                self.logger.info("✅ Notifica debiti inviata al canale admin")
            except Exception as e:
                self.logger.error(f"❌ Errore invio notifica debiti al canale admin: {e}")

        # Invia a tutti gli admin IDs
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                self.logger.info(f"✅ Notifica debiti inviata all'admin {admin_id}")
            except Exception as e:
                self.logger.error(f"❌ Errore invio notifica debiti all'admin {admin_id}: {e}")

        # CORREZIONE: Invia anche al proprietario se diverso dagli admin
        if self.owner_id and self.owner_id not in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=self.owner_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                self.logger.info(f"✅ Notifica debiti inviata al proprietario {self.owner_id}")
            except Exception as e:
                self.logger.error(f"❌ Errore invio notifica debiti al proprietario: {e}")

    async def handle_unauthorized_group_join(self, chat_id: int, chat_title: str, user_id: Optional[int] = None):
        """
        Gestisce l'aggiunta del bot a gruppi non autorizzati con sistema blacklist corretto.
        CORREZIONE: Evita notifiche duplicate e gestisce correttamente la blacklist.
        """
        # Inizializza gruppo nel tracking blacklist se non esiste
        if chat_id not in self.group_blacklist:
            self.group_blacklist[chat_id] = {
                "attempts": 0,
                "blacklisted": False,
                "last_attempt_at": None,
            }

        entry = self.group_blacklist[chat_id]
        now = datetime.now(timezone.utc)
        last_attempt_at = entry.get("last_attempt_at")
        if last_attempt_at and (now - last_attempt_at).total_seconds() < self.duplicate_interval_seconds:
            entry["last_attempt_at"] = now
            self.logger.debug(
                "Tentativo duplicato ignorato per il gruppo non autorizzato %s", chat_id
            )
            return

        entry["last_attempt_at"] = now

        # Incrementa tentativi
        entry["attempts"] += 1
        attempts = entry["attempts"]

        # Controlla se deve essere inserito in blacklist
        should_blacklist = attempts >= self.max_attempts

        if should_blacklist and not entry["blacklisted"]:
            # Inserisce in blacklist
            entry["blacklisted"] = True
            message = (
                f"🚫 **GRUPPO INSERITO IN BLACKLIST**\n\n"
                f"👥 **Gruppo:** {chat_title}\n"
                f"🆔 **Chat ID:** `{chat_id}`\n"
                f"🔢 **Tentativi:** {attempts}/{self.max_attempts}\n\n"
                f"⚠️ **Il gruppo è stato inserito nella blacklist dopo {self.max_attempts} tentativi**\n"
                f"📞 **Per sbloccare contattare:** @sciadouu\n\n"
                f"📅 **Timestamp:** {self.get_local_timestamp()}"
            )
        elif not should_blacklist:
            # Gruppo non ancora in blacklist
            message = (
                f"🚫 **ACCESSO GRUPPO NON AUTORIZZATO**\n\n"
                f"👥 **Gruppo:** {chat_title}\n"
                f"🆔 **Chat ID:** `{chat_id}`\n"
                f"🔢 **Tentativo:** {attempts}/{self.max_attempts}\n\n"
                f"⚠️ **Dopo {self.max_attempts} tentativi il gruppo verrà inserito nella blacklist**\n"
                f"📞 **Per sbloccare contattare:** @sciadouu\n\n"
                f"✅ **Azione:** Bot uscito automaticamente dal gruppo\n"
                f"📅 **Timestamp:** {self.get_local_timestamp()}"
            )
        else:
            # Gruppo è in blacklist, non inviare notifica aggiuntiva
            self.logger.info(f"Gruppo {chat_id} in blacklist, notifica saltata")
            return

        # Invia notifica solo al proprietario (OWNER_CHAT_ID)
        if self.owner_id:
            try:
                await self.bot.send_message(
                    chat_id=self.owner_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                self.logger.info(f"Notifica gruppo non autorizzato inviata al proprietario")
            except Exception as e:
                self.logger.error(f"Errore invio notifica gruppo non autorizzato: {e}")

    # ================================================================================
    # METODI MANCANTI - Risolve l'AttributeError
    # ================================================================================

    async def send_bot_status_update(self, status: str, details: str = ""):
        """
        METODO MANCANTE - Invia notifica per aggiornamenti stato bot.
        """
        message = f"🤖 **BOT STATUS: {status}**\n\n"

        if details:
            message += f"📋 **Dettagli:** {details}\n\n"

        message += f"📅 **Timestamp:** {self.get_local_timestamp()}"

        # Invia al canale admin
        if self.admin_channel_id:
            try:
                await self.bot.send_message(
                    chat_id=self.admin_channel_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            except Exception as e:
                self.logger.error(f"Errore invio bot status al canale admin: {e}")

        # Invia al proprietario se è un update critico
        if self.owner_id and ("ERRORE" in status.upper() or "CRITICAL" in status.upper()):
            try:
                await self.bot.send_message(
                    chat_id=self.owner_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            except Exception as e:
                self.logger.error(f"Errore invio bot status al proprietario: {e}")

    async def send_unauthorized_group_alert(self, chat_id: int, chat_title: str, user_id: Optional[int] = None, username: Optional[str] = None):
        """
        METODO MANCANTE - Compatibilità con il codice esistente.
        """
        await self.handle_unauthorized_group_join(chat_id, chat_title, user_id)

    # ================================================================================
    # METODI UTILITY E COMPATIBILITÀ
    # ================================================================================

    def is_group_blacklisted(self, chat_id: int) -> bool:
        """Verifica se un gruppo è in blacklist"""
        return self.group_blacklist.get(chat_id, {}).get("blacklisted", False)

    async def send_admin_notification(self, message: str, notification_type: NotificationType = NotificationType.INFO, urgent: bool = False, disable_rate_limit: bool = False):
        """
        Invia notifica generica agli admin con timestamp corretto.
        """
        # Rate limiting check (tranne per messaggi critici o urgenti)
        if not disable_rate_limit and not urgent and notification_type not in [NotificationType.CRITICAL, NotificationType.SECURITY]:
            if await self._is_rate_limited(notification_type):
                self.logger.debug(f"Notifica rate limited: {notification_type}")
                return

        # Aggiunge timestamp corretto al messaggio
        timestamped_message = f"{message}\n\n📅 **Timestamp:** {self.get_local_timestamp()}"

        # Formatta messaggio con emoji
        formatted_message = f"{notification_type.value} **NOTIFICA BOT**\n\n{timestamped_message}"

        # Invia al canale admin
        if self.admin_channel_id:
            try:
                await self.bot.send_message(
                    chat_id=self.admin_channel_id,
                    text=formatted_message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            except Exception as e:
                self.logger.error(f"Errore invio al canale admin: {e}")

        # Invia agli admin se urgente
        if urgent:
            for admin_id in self.admin_ids:
                try:
                    await self.bot.send_message(
                        chat_id=admin_id,
                        text=formatted_message,
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    self.logger.error(f"Errore invio all'admin {admin_id}: {e}")

        # Aggiorna timestamp ultimo invio
        self.last_notification_time[notification_type] = datetime.now()

    async def send_authorized_group_notification(self, chat_id: int, chat_title: str):
        """Invia notifica quando il bot viene aggiunto a un gruppo autorizzato"""
        message = (
            f"✅ **BOT AGGIUNTO A GRUPPO AUTORIZZATO**\n\n"
            f"👥 **Gruppo:** {chat_title}\n"
            f"🆔 **Chat ID:** `{chat_id}`\n\n"
            f"🤖 **Status:** Bot attivo e operativo"
        )
        await self.send_admin_notification(message, NotificationType.SUCCESS)

    # Rate limiting per compatibilità con codice esistente
    async def _is_rate_limited(self, notification_type: NotificationType) -> bool:
        """Controlla se la notifica è rate limited"""
        if notification_type not in self.last_notification_time:
            return False

        time_diff = (datetime.now() - self.last_notification_time[notification_type]).seconds
        return time_diff < self.min_interval

# Classe per compatibilità con il nome originale se necessario
class NotificationService(EnhancedNotificationService):
    """Alias per compatibilità con il codice esistente"""
    pass