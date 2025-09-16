import logging
import logging.handlers
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import json

class TelegramLogHandler(logging.Handler):
    """Handler personalizzato per inviare log critici su Telegram"""
    
    def __init__(self, bot, chat_ids: List[int], min_level: int = logging.ERROR):
        super().__init__()
        self.bot = bot
        self.chat_ids = chat_ids
        self.min_level = min_level
        self.setLevel(min_level)
        
    def emit(self, record):
        """Invia record di log su Telegram se supera il livello minimo"""
        if record.levelno >= self.min_level:
            log_message = self.format(record)
            
            # Invia asincrono senza bloccare il thread principale
            asyncio.create_task(self._send_telegram_log(log_message, record.levelname))
            
    async def _send_telegram_log(self, message: str, level: str):
        """Invia log su Telegram con formattazione appropriata"""
        
        # Emoji per livello
        level_emoji = {
            'ERROR': '🚨',
            'CRITICAL': '💥', 
            'WARNING': '⚠️'
        }
        
        emoji = level_emoji.get(level, '📋')
        
        # Tronca messaggio se troppo lungo
        if len(message) > 3500:
            message = message[:3500] + "... [TRONCATO]"
        
        formatted_message = (
            f"{emoji} **LOG {level}**\n\n"
            f"```\n{message}\n```\n\n"
            f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
        )
        
        for chat_id in self.chat_ids:
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=formatted_message,
                    parse_mode="Markdown"
                )
            except Exception as e:
                # Evita loop di logging
                print(f"Errore invio log Telegram a {chat_id}: {e}")

class BotLogger:
    """
    Sistema di logging avanzato per il bot Wolvesville.
    
    Features:
    - Log rotazionali con retention automatica
    - Handler multipli (console, file, Telegram)
    - Livelli configurabili
    - Logging strutturato per azioni utente, API calls, errori
    - Statistiche di utilizzo
    - Performance monitoring
    """
    
    def __init__(self, name: str = "WolvesvilleBot", log_dir: str = "data/logs"):
        self.logger = logging.getLogger(name)
        self.log_dir = Path(log_dir)
        self.setup_logging()
        
        # Contatori per statistiche
        self.stats = {
            'user_actions': 0,
            'api_calls': 0, 
            'errors': 0,
            'warnings': 0,
            'start_time': datetime.now()
        }
        
    def setup_logging(self):
        """Configura il sistema di logging completo"""
        
        # Evita configurazione multipla
        if self.logger.handlers:
            return
            
        self.logger.setLevel(logging.INFO)
        
        # Crea directory logs se non esiste
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Handler file principale con rotazione
        main_file_handler = logging.handlers.RotatingFileHandler(
            filename=self.log_dir / "bot.log",
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        
        # Handler file errori separato
        error_file_handler = logging.handlers.RotatingFileHandler(
            filename=self.log_dir / "errors.log",
            maxBytes=5*1024*1024,  # 5MB
            backupCount=3,
            encoding='utf-8'
        )
        error_file_handler.setLevel(logging.ERROR)
        
        # Handler file azioni utente
        user_file_handler = logging.handlers.RotatingFileHandler(
            filename=self.log_dir / "user_actions.log",
            maxBytes=5*1024*1024,  # 5MB
            backupCount=3,
            encoding='utf-8'
        )
        
        # Handler console
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        
        # Formatter dettagliato per file
        detailed_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        )
        
        # Formatter semplice per console
        simple_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        )
        
        # Applica formatter
        main_file_handler.setFormatter(detailed_formatter)
        error_file_handler.setFormatter(detailed_formatter)
        user_file_handler.setFormatter(detailed_formatter)
        console_handler.setFormatter(simple_formatter)
        
        # Aggiungi handlers
        self.logger.addHandler(main_file_handler)
        self.logger.addHandler(error_file_handler)
        self.logger.addHandler(user_file_handler)
        self.logger.addHandler(console_handler)
        
        self.logger.info("Sistema logging inizializzato")
        
    def add_telegram_handler(self, bot, admin_chat_ids: List[int], min_level: int = logging.ERROR):
        """Aggiunge handler Telegram per log critici"""
        telegram_handler = TelegramLogHandler(bot, admin_chat_ids, min_level)
        telegram_handler.setLevel(min_level)
        self.logger.addHandler(telegram_handler)
        self.logger.info("Handler Telegram aggiunto al sistema di logging")
        
    def log_user_action(self, user_id: int, action: str, details: str = "", chat_type: str = "private"):
        """Log azione utente con contesto completo"""
        self.stats['user_actions'] += 1
        
        message = f"USER_ACTION | User: {user_id} | Chat: {chat_type} | Action: {action}"
        if details:
            message += f" | Details: {details}"
            
        self.logger.info(message)
        
    def log_api_call(self, endpoint: str, status_code: int, response_time: float, method: str = "GET"):
        """Log chiamata API con metriche performance"""
        self.stats['api_calls'] += 1
        
        message = f"API_CALL | Method: {method} | Endpoint: {endpoint} | Status: {status_code} | Time: {response_time:.2f}s"
        
        if status_code >= 400:
            self.logger.warning(message)
            if status_code >= 500:
                self.stats['errors'] += 1
        else:
            self.logger.info(message)
            
    def log_database_operation(self, operation: str, collection: str, result: str = "", duration: float = 0):
        """Log operazione database con metriche"""
        message = f"DB_OPERATION | Operation: {operation} | Collection: {collection}"
        
        if result:
            message += f" | Result: {result}"
        if duration > 0:
            message += f" | Duration: {duration:.3f}s"
            
        self.logger.info(message)
        
    def log_error(self, error: Exception, context: str = "", user_id: Optional[int] = None):
        """Log errore con contesto completo"""
        self.stats['errors'] += 1
        
        message = f"ERROR | Context: {context}"
        if user_id:
            message += f" | User: {user_id}"
        message += f" | Error: {str(error)}"
        
        self.logger.error(message, exc_info=True)
        
    def log_security_event(self, event_type: str, details: str, user_id: Optional[int] = None, chat_id: Optional[int] = None):
        """Log evento di sicurezza"""
        message = f"SECURITY | Event: {event_type} | Details: {details}"
        
        if user_id:
            message += f" | User: {user_id}"
        if chat_id:
            message += f" | Chat: {chat_id}"
            
        self.logger.warning(message)
        self.stats['warnings'] += 1
        
    def log_scheduler_job(self, job_name: str, execution_time: float, success: bool = True, error: str = ""):
        """Log esecuzione job scheduler"""
        status = "SUCCESS" if success else "FAILED"
        message = f"SCHEDULER | Job: {job_name} | Status: {status} | Time: {execution_time:.2f}s"
        
        if not success and error:
            message += f" | Error: {error}"
            self.logger.error(message)
        else:
            self.logger.info(message)
        
    def get_stats(self) -> dict:
        """Ritorna statistiche utilizzo logger"""
        uptime = (datetime.now() - self.stats['start_time']).total_seconds()
        
        return {
            **self.stats,
            'uptime_seconds': uptime,
            'uptime_hours': uptime / 3600,
            'timestamp': datetime.now().isoformat()
        }
        
    def save_daily_stats(self):
        """Salva statistiche giornaliere su file"""
        stats_file = self.log_dir / f"stats_{datetime.now().strftime('%Y_%m_%d')}.json"
        
        with open(stats_file, 'w') as f:
            json.dump(self.get_stats(), f, indent=2)
            
        self.logger.info(f"Statistiche giornaliere salvate in {stats_file}")
        
        # Reset contatori per il giorno successivo (eccetto start_time)
        start_time = self.stats['start_time']
        for key in self.stats:
            if key != 'start_time':
                self.stats[key] = 0
        self.stats['start_time'] = start_time

# Istanza globale logger
bot_logger = BotLogger()
