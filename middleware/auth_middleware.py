from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram import types
import logging
from typing import Callable, Dict, Any, Awaitable

class GroupAuthorizationMiddleware(BaseMiddleware):
    """
    Middleware per controllo autorizzazione gruppi.
    
    Funzionalità:
    - Verifica se il bot è stato aggiunto a gruppi autorizzati
    - Esce automaticamente da gruppi non autorizzati  
    - Invia notifiche agli admin
    - Log di tutte le operazioni di sicurezza
    """
    
    def __init__(self, authorized_groups: set, admin_ids: list, notification_service=None):
        self.authorized_groups = set(authorized_groups)
        self.admin_ids = admin_ids
        self.notification_service = notification_service
        self.logger = logging.getLogger(__name__)
        super().__init__()
        
    async def __call__(
        self,
        handler: Callable[[types.Update, Dict[str, Any]], Awaitable[Any]],
        event: types.Update,
        data: Dict[str, Any]
    ) -> Any:
        
        # Gestione eventi chat_member (bot aggiunto/rimosso)
        if event.my_chat_member:
            await self._handle_chat_member_update(event.my_chat_member)
            return await handler(event, data)
            
        # Controllo messaggi in gruppi
        if event.message:
            if await self._check_group_authorization(event.message):
                return await handler(event, data)
            else:
                return  # Blocca elaborazione per gruppo non autorizzato
                
        return await handler(event, data)
    
    async def _handle_chat_member_update(self, chat_member_update: types.ChatMemberUpdated):
        """Gestisce aggiunta/rimozione bot da gruppi"""
        chat_id = chat_member_update.chat.id
        chat_title = chat_member_update.chat.title or f"Chat {chat_id}"
        new_status = chat_member_update.new_chat_member.status
        
        # Bot aggiunto a gruppo
        if new_status in ['member', 'administrator']:
            if chat_id not in self.authorized_groups:
                self.logger.warning(f"Bot aggiunto a gruppo non autorizzato: {chat_title} ({chat_id})")
                await self._handle_unauthorized_group(chat_member_update)
            else:
                self.logger.info(f"Bot aggiunto a gruppo autorizzato: {chat_title} ({chat_id})")
                
        # Bot rimosso da gruppo  
        elif new_status in ['left', 'kicked']:
            self.logger.info(f"Bot rimosso da gruppo: {chat_title} ({chat_id})")
    
    async def _check_group_authorization(self, message: types.Message) -> bool:
        """Controlla se il messaggio proviene da gruppo autorizzato"""
        if message.chat.type not in ['group', 'supergroup']:
            return True  # Chat private sempre autorizzate
            
        chat_id = message.chat.id
        if chat_id not in self.authorized_groups:
            self.logger.warning(f"Messaggio da gruppo non autorizzato: {message.chat.title} ({chat_id})")
            await self._handle_unauthorized_group_message(message)
            return False
            
        return True
    
    async def _handle_unauthorized_group(self, chat_member_update: types.ChatMemberUpdated):
        """Gestisce accesso a gruppo non autorizzato"""
        bot = chat_member_update.bot
        chat_id = chat_member_update.chat.id
        chat_title = chat_member_update.chat.title or f"Chat {chat_id}"
        
        # Invia notifica admin
        if self.notification_service:
            await self.notification_service.send_unauthorized_group_alert(
                chat_id=chat_id,
                chat_title=chat_title,
                user_id=chat_member_update.from_user.id if chat_member_update.from_user else None,
                username=chat_member_update.from_user.username if chat_member_update.from_user else None
            )
        
        # Esce dal gruppo
        try:
            await bot.leave_chat(chat_id)
            self.logger.info(f"Bot uscito automaticamente da gruppo non autorizzato: {chat_title}")
        except Exception as e:
            self.logger.error(f"Errore uscita da gruppo {chat_id}: {e}")
    
    async def _handle_unauthorized_group_message(self, message: types.Message):
        """Gestisce messaggio da gruppo non autorizzato"""
        # Per messaggi in gruppi non autorizzati, esce comunque
        try:
            await message.bot.leave_chat(message.chat.id)
            self.logger.info(f"Bot uscito da gruppo non autorizzato dopo messaggio")
        except Exception as e:
            self.logger.error(f"Errore uscita da gruppo: {e}")