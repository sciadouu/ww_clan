import logging
import asyncio
import traceback
from typing import Optional, Any, Dict, Type
from datetime import datetime, timedelta
from functools import wraps
import aiohttp
from aiogram import types
from aiogram.exceptions import (
    TelegramBadRequest, 
    TelegramNotFound, 
    TelegramForbiddenError,
    TelegramServerError,
    TelegramRetryAfter,
    TelegramConflictError,
    TelegramUnauthorizedError
)

class BotErrorHandler:
    """
    Sistema di gestione errori avanzato per bot Telegram
    
    FEATURES:
    ✅ Gestione specifica per ogni tipo di errore Telegram
    ✅ Retry automatico con backoff exponenziale
    ✅ Logging strutturato degli errori
    ✅ Notifiche admin per errori critici
    ✅ Rate limiting per prevenire flood
    ✅ Graceful degradation per API esterne
    """
    
    def __init__(self, bot, admin_chat_ids: list, logger: logging.Logger):
        self.bot = bot
        self.admin_chat_ids = admin_chat_ids
        self.logger = logger
        
        # Contatori per rate limiting
        self.error_counts = {}
        self.last_notification = {}
        
        # Configurazione retry
        self.max_retries = 3
        self.base_delay = 1  # secondi
        
    async def handle_telegram_error(self, error: Exception, context: Dict[str, Any] = None) -> Optional[str]:
        """
        Gestisce errori specifici dell'API Telegram con retry automatico
        
        Args:
            error: L'eccezione da gestire
            context: Contesto aggiuntivo (user_id, command, etc.)
        
        Returns:
            Messaggio user-friendly o None se gestito silenziosamente
        """
        
        error_context = {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "timestamp": datetime.now().isoformat(),
            **(context or {})
        }
        
        # Gestione specifica per tipo di errore
        if isinstance(error, TelegramRetryAfter):
            return await self._handle_retry_after(error, error_context)
            
        elif isinstance(error, TelegramBadRequest):
            return await self._handle_bad_request(error, error_context)
            
        elif isinstance(error, TelegramNotFound):
            return await self._handle_not_found(error, error_context)
            
        elif isinstance(error, TelegramForbiddenError):
            return await self._handle_forbidden(error, error_context)
            
        elif isinstance(error, TelegramServerError):
            return await self._handle_server_error(error, error_context)
            
        elif isinstance(error, TelegramUnauthorizedError):
            return await self._handle_unauthorized(error, error_context)
            
        elif isinstance(error, TelegramConflictError):
            return await self._handle_conflict(error, error_context)
            
        else:
            return await self._handle_generic_error(error, error_context)
    
    async def _handle_retry_after(self, error: TelegramRetryAfter, context: Dict) -> str:
        """Gestisce flood control di Telegram"""
        
        retry_after = error.retry_after
        
        self.logger.warning(
            "Rate limit exceeded",
            extra={
                "retry_after": retry_after,
                "user_id": context.get("user_id"),
                "command": context.get("command")
            }
        )
        
        # Attendi il tempo richiesto da Telegram
        await asyncio.sleep(retry_after)
        
        return f"⏳ Troppe richieste! Riprova tra {retry_after} secondi."
    
    async def _handle_bad_request(self, error: TelegramBadRequest, context: Dict) -> str:
        """Gestisce richieste malformate"""
        
        error_msg = str(error)
        
        # Errori comuni con messaggi user-friendly
        if "message is not modified" in error_msg:
            self.logger.debug("Tentativo di modificare messaggio identico", extra=context)
            return None  # Gestito silenziosamente
            
        elif "message to delete not found" in error_msg:
            self.logger.debug("Messaggio da eliminare non trovato", extra=context)
            return None
            
        elif "can't parse entities" in error_msg:
            self.logger.warning("Errore parsing markdown/HTML", extra=context)
            return "❌ Formato messaggio non valido. Riprova senza caratteri speciali."
            
        elif "message is too long" in error_msg:
            self.logger.warning("Messaggio troppo lungo", extra=context)
            return "❌ Messaggio troppo lungo. Riduci il testo."
            
        else:
            self.logger.error("Bad request generico", extra=context)
            await self._notify_admin_error(error, context)
            return "❌ Richiesta non valida. Riprova."
    
    async def _handle_not_found(self, error: TelegramNotFound, context: Dict) -> str:
        """Gestisce risorse non trovate"""
        
        self.logger.info("Risorsa non trovata", extra=context)
        
        if "chat not found" in str(error):
            return "❌ Chat non trovata. Verifica l'ID chat."
        elif "user not found" in str(error):
            return "❌ Utente non trovato."
        elif "message not found" in str(error):
            return None  # Gestito silenziosamente
        else:
            return "❌ Elemento non trovato."
    
    async def _handle_forbidden(self, error: TelegramForbiddenError, context: Dict) -> str:
        """Gestisce errori di permessi"""
        
        user_id = context.get("user_id")
        
        if "bot was blocked by the user" in str(error):
            self.logger.info(f"Bot bloccato dall'utente {user_id}", extra=context)
            return None  # Non possiamo inviare messaggi all'utente
            
        elif "chat member status is not administrator" in str(error):
            self.logger.warning("Permessi insufficienti in chat", extra=context)
            return "❌ Il bot non ha i permessi necessari in questa chat."
            
        else:
            self.logger.warning("Accesso negato", extra=context)
            return "❌ Accesso negato. Verifica i permessi."
    
    async def _handle_server_error(self, error: TelegramServerError, context: Dict) -> str:
        """Gestisce errori del server Telegram"""
        
        self.logger.error("Errore server Telegram", extra=context)
        await self._notify_admin_error(error, context)
        
        # Retry automatico per errori server
        for attempt in range(self.max_retries):
            delay = self.base_delay * (2 ** attempt)  # Backoff exponenziale
            await asyncio.sleep(delay)
            
            self.logger.info(f"Retry tentativo {attempt + 1}/{self.max_retries}")
            # Il retry sarà gestito dal chiamante
            
        return "🔧 Problemi temporanei del server. Riprova più tardi."
    
    async def _handle_unauthorized(self, error: TelegramUnauthorizedError, context: Dict) -> str:
        """Gestisce token bot non valido"""
        
        self.logger.critical("Token bot non valido!", extra=context)
        await self._notify_admin_critical("🚨 TOKEN BOT NON VALIDO! Controlla immediatamente.")
        
        return "🚨 Errore di autorizzazione critico. Contatta l'amministratore."
    
    async def _handle_conflict(self, error: TelegramConflictError, context: Dict) -> str:
        """Gestisce conflitti (bot già in uso)"""
        
        self.logger.critical("Conflict error - Bot già in uso!", extra=context)
        await self._notify_admin_critical("🚨 Bot già in esecuzione in un'altra istanza!")
        
        return None  # L'applicazione dovrebbe terminare
    
    async def _handle_generic_error(self, error: Exception, context: Dict) -> str:
        """Gestisce errori generici non Telegram"""
        
        self.logger.error(
            "Errore generico non gestito",
            extra={
                **context,
                "traceback": traceback.format_exc()
            }
        )
        
        await self._notify_admin_error(error, context)
        return "❌ Si è verificato un errore. L'amministratore è stato notificato."
    
    async def _notify_admin_error(self, error: Exception, context: Dict):
        """Invia notifica errore agli admin (con rate limiting)"""
        
        error_key = f"{type(error).__name__}_{context.get('user_id', 'unknown')}"
        now = datetime.now()
        
        # Rate limiting: max 1 notifica dello stesso tipo ogni 5 minuti
        if error_key in self.last_notification:
            time_diff = now - self.last_notification[error_key]
            if time_diff < timedelta(minutes=5):
                return
        
        self.last_notification[error_key] = now
        
        message = (
            f"🚨 **ERRORE BOT**\n\n"
            f"**Tipo**: {type(error).__name__}\n"
            f"**Messaggio**: {str(error)}\n"
            f"**User ID**: {context.get('user_id', 'N/A')}\n"
            f"**Comando**: {context.get('command', 'N/A')}\n"
            f"**Timestamp**: {context.get('timestamp')}\n\n"
            f"**Contesto aggiuntivo**:\n"
            f"```json\n{context}\n```"
        )
        
        for admin_id in self.admin_chat_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    parse_mode="Markdown"
                )
            except Exception as e:
                self.logger.error(f"Errore invio notifica admin {admin_id}: {e}")
    
    async def _notify_admin_critical(self, message: str):
        """Invia notifica critica immediata agli admin"""
        
        critical_msg = f"🆘 **ERRORE CRITICO**\n\n{message}\n\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        for admin_id in self.admin_chat_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=critical_msg,
                    parse_mode="Markdown"
                )
            except Exception as e:
                self.logger.error(f"Errore invio notifica critica admin {admin_id}: {e}")

class APIErrorHandler:
    """
    Gestione errori per chiamate API esterne (Wolvesville, MongoDB, etc.)
    """
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        
    async def handle_api_error(self, error: Exception, api_name: str, endpoint: str = "", context: Dict = None) -> Dict[str, Any]:
        """
        Gestisce errori API esterne con retry e graceful degradation
        
        Returns:
            Dict con 'success', 'error_type', 'message', 'retry_recommended'
        """
        
        error_info = {
            "success": False,
            "error_type": type(error).__name__,
            "message": "Errore generico API",
            "retry_recommended": False,
            "api_name": api_name,
            "endpoint": endpoint,
            "context": context or {}
        }
        
        if isinstance(error, aiohttp.ClientError):
            return await self._handle_http_error(error, error_info)
        elif isinstance(error, asyncio.TimeoutError):
            return await self._handle_timeout_error(error, error_info)
        else:
            return await self._handle_generic_api_error(error, error_info)
    
    async def _handle_http_error(self, error: aiohttp.ClientError, info: Dict) -> Dict:
        """Gestisce errori HTTP specifici"""
        
        if isinstance(error, aiohttp.ClientConnectorError):
            self.logger.warning(f"Connessione fallita a {info['api_name']}", extra=info)
            info.update({
                "message": f"Impossibile connettersi a {info['api_name']}",
                "retry_recommended": True
            })
            
        elif isinstance(error, aiohttp.ClientTimeout):
            self.logger.warning(f"Timeout {info['api_name']}", extra=info)
            info.update({
                "message": f"Timeout connessione a {info['api_name']}",
                "retry_recommended": True
            })
            
        elif isinstance(error, aiohttp.ClientResponseError):
            status = getattr(error, 'status', 0)
            
            if 400 <= status < 500:
                # Errori client - generalmente non retry
                self.logger.warning(f"Errore client {status} per {info['api_name']}", extra=info)
                info.update({
                    "message": f"Richiesta non valida per {info['api_name']} ({status})",
                    "retry_recommended": False
                })
            elif 500 <= status < 600:
                # Errori server - retry consigliato
                self.logger.error(f"Errore server {status} per {info['api_name']}", extra=info)
                info.update({
                    "message": f"Errore server {info['api_name']} ({status})",
                    "retry_recommended": True
                })
        
        return info
    
    async def _handle_timeout_error(self, error: asyncio.TimeoutError, info: Dict) -> Dict:
        """Gestisce timeout"""
        
        self.logger.warning(f"Timeout {info['api_name']}", extra=info)
        info.update({
            "message": f"Timeout per {info['api_name']}",
            "retry_recommended": True
        })
        
        return info
    
    async def _handle_generic_api_error(self, error: Exception, info: Dict) -> Dict:
        """Gestisce errori API generici"""
        
        self.logger.error(f"Errore generico {info['api_name']}: {error}", extra=info)
        info.update({
            "message": f"Errore interno {info['api_name']}",
            "retry_recommended": True
        })
        
        return info

def with_error_handling(handler_func):
    """
    Decoratore per gestire automaticamente errori nei handler
    """
    
    @wraps(handler_func)
    async def wrapper(message_or_callback, *args, **kwargs):
        try:
            return await handler_func(message_or_callback, *args, **kwargs)
            
        except Exception as e:
            # Determina se è message o callback
            if hasattr(message_or_callback, 'from_user'):
                user_id = message_or_callback.from_user.id
                
                if hasattr(message_or_callback, 'text'):
                    # È un Message
                    command = message_or_callback.text.split()[0] if message_or_callback.text else "unknown"
                    context = {
                        "user_id": user_id,
                        "command": command,
                        "chat_type": message_or_callback.chat.type,
                        "handler": handler_func.__name__
                    }
                    
                    # Usa il gestore errori globale
                    from config import error_handler  # Importa il gestore globale
                    error_msg = await error_handler.handle_telegram_error(e, context)
                    
                    if error_msg:
                        await message_or_callback.answer(error_msg)
                        
                else:
                    # È un CallbackQuery
                    context = {
                        "user_id": user_id,
                        "callback_data": message_or_callback.data,
                        "handler": handler_func.__name__
                    }
                    
                    from config import error_handler
                    error_msg = await error_handler.handle_telegram_error(e, context)
                    
                    if error_msg:
                        await message_or_callback.message.answer(error_msg)
                        
                    # Acknowledge callback per evitare "loading" infinito
                    try:
                        await message_or_callback.answer()
                    except:
                        pass
            else:
                # Contesto sconosciuto
                logging.error(f"Errore in {handler_func.__name__}: {e}")
                
    return wrapper

# Funzioni helper per retry automatico
async def retry_api_call(api_func, max_retries: int = 3, base_delay: float = 1.0, *args, **kwargs):
    """
    Esegue una chiamata API con retry automatico
    
    Args:
        api_func: Funzione API da chiamare
        max_retries: Numero massimo di tentativi
        base_delay: Delay base per backoff exponenziale
        *args, **kwargs: Parametri per api_func
    
    Returns:
        Risultato della chiamata API
    """
    
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return await api_func(*args, **kwargs)
            
        except Exception as e:
            last_exception = e
            
            if attempt == max_retries:
                break
                
            # Calcola delay con backoff exponenziale + jitter
            delay = base_delay * (2 ** attempt) + (random.random() * 0.5)
            
            logging.warning(f"Tentativo {attempt + 1} fallito, retry in {delay:.2f}s: {e}")
            await asyncio.sleep(delay)
    
    # Tutti i tentativi falliti
    raise last_exception