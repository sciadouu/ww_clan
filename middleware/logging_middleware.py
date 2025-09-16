from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram import types
from typing import Callable, Dict, Any, Awaitable
import time
from utils.logger import bot_logger

class LoggingMiddleware(BaseMiddleware):
    """
    Middleware per logging avanzato di tutti gli eventi del bot.
    
    Funzionalità:
    - Log di tutti i messaggi ricevuti con dettagli
    - Tracking performance handler
    - Log eventi speciali (callback, join/leave)
    - Statistiche utilizzo real-time
    """
    
    def __init__(self):
        super().__init__()
        
    async def __call__(
        self,
        handler: Callable[[types.Update, Dict[str, Any]], Awaitable[Any]],
        event: types.Update,
        data: Dict[str, Any]
    ) -> Any:
        
        start_time = time.time()
        
        # Log evento ricevuto
        await self._log_incoming_event(event)
        
        try:
            # Esegui handler
            result = await handler(event, data)
            
            # Log successo
            execution_time = time.time() - start_time
            await self._log_handler_success(event, execution_time)
            
            return result
            
        except Exception as e:
            # Log errore
            execution_time = time.time() - start_time 
            await self._log_handler_error(event, e, execution_time)
            raise
    
    async def _log_incoming_event(self, event: types.Update):
        """Log evento in arrivo con dettagli completi"""
        
        if event.message:
            msg = event.message
            chat_type = msg.chat.type
            user_id = msg.from_user.id if msg.from_user else None
            username = msg.from_user.username if msg.from_user else "N/A"
            text = msg.text[:50] + "..." if msg.text and len(msg.text) > 50 else msg.text or "<Non testuale>"
            
            bot_logger.log_user_action(
                user_id=user_id or 0,
                action="MESSAGE",
                details=f"Text: '{text}' | Username: {username}",
                chat_type=chat_type
            )
            
        elif event.callback_query:
            cb = event.callback_query
            user_id = cb.from_user.id
            username = cb.from_user.username or "N/A"
            callback_data = cb.data[:30] + "..." if cb.data and len(cb.data) > 30 else cb.data or "N/A"
            
            bot_logger.log_user_action(
                user_id=user_id,
                action="CALLBACK",
                details=f"Data: '{callback_data}' | Username: {username}",
                chat_type="callback"
            )
            
        elif event.my_chat_member:
            chat_member = event.my_chat_member
            chat_title = chat_member.chat.title or f"Chat {chat_member.chat.id}"
            new_status = chat_member.new_chat_member.status
            old_status = chat_member.old_chat_member.status if chat_member.old_chat_member else "N/A"
            
            bot_logger.log_security_event(
                event_type="CHAT_MEMBER_UPDATE",
                details=f"Chat: {chat_title} | Status: {old_status} -> {new_status}",
                chat_id=chat_member.chat.id
            )
    
    async def _log_handler_success(self, event: types.Update, execution_time: float):
        """Log esecuzione handler riuscita con metriche performance"""
        
        if execution_time > 1.0:  # Log solo se lento
            bot_logger.logger.warning(f"SLOW_HANDLER | Execution time: {execution_time:.2f}s")
            
        elif execution_time > 0.5:
            bot_logger.logger.info(f"HANDLER_PERF | Execution time: {execution_time:.3f}s")
    
    async def _log_handler_error(self, event: types.Update, error: Exception, execution_time: float):
        """Log errore handler con contesto completo"""
        
        context = "UNKNOWN"
        user_id = None
        
        if event.message:
            context = "MESSAGE_HANDLER"
            user_id = event.message.from_user.id if event.message.from_user else None
        elif event.callback_query:
            context = "CALLBACK_HANDLER" 
            user_id = event.callback_query.from_user.id
        elif event.my_chat_member:
            context = "CHAT_MEMBER_HANDLER"
            
        bot_logger.log_error(
            error=error,
            context=f"{context} | Duration: {execution_time:.3f}s",
            user_id=user_id
        )