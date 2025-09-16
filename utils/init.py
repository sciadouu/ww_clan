"""
Utils package per il bot Telegram Wolvesville
"""

# Import sicuro con fallback
try:
    from .logger import BotLogger, bot_logger
    print("✅ bot_logger importato correttamente da utils")
    __all__ = ['BotLogger', 'bot_logger']
except ImportError as e:
    print(f"❌ Errore import logger: {e}")
    BotLogger = None
    bot_logger = None
    __all__ = []