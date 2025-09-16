from typing import Final

# Configurazione del bot
TOKEN = "7649312296:AAEGUQ1PKeDhtfRarlJxI-zOxlcRC5fQ1p8"
OWNER_CHAT_ID: Final = 7020291568  # ID della chat dell'owner
WAIT_TIME_SECONDS = 5  # Tempo di attesa prima di uscire dai gruppi non autorizzati
BOT_USERNAME: Final = '@Disadattati_Bot'  # Nome utente del bot

# API Key per Wolvesville
API_KEY = 'ffeOggTEIZKwkWVHJnIbBhWiSfL06bNg65BIgt4chW21tVXUybesZzIaAguYXzfk'
WOLVESVILLE_API_KEY = "ffeOggTEIZKwkWVHJnIbBhWiSfL06bNg65BIgt4chW21tVXUybesZzIaAguYXzfk"
# URL base dell'API di Wolvesville
WOLVESVILLE_API_URL = 'https://api.wolvesville.com'
# Locale per le richieste
LOCALE = 'it'


# Gruppi autorizzati (sostituisci con i tuoi chat ID)
AUTHORIZED_GROUPS = [-1002383442316, -4094606556]  # Lista gruppi dove il bot può stare
ADMIN_IDS = [7020291568]  # I tuoi admin ID
# Canale notifiche admin (opzionale - lascia None se non hai un canale dedicato)
ADMIN_NOTIFICATION_CHANNEL = -4094606556    # es: -1001234567890

# Sistema logging
LOG_LEVEL = "INFO"
LOG_RETENTION_DAYS = 30
TELEGRAM_LOG_HANDLER = True

# Rate limiting notifiche
NOTIFICATION_RATE_LIMIT = 5  # secondi tra notifiche dello stesso tipo

# Clan ID
CLAN_ID = '25d3ed14-a4bd-4e76-a844-2b534cb2f5bd'  # ID del clan
# Password per l'accesso ai comandi protetti
BOT_PASSWORD = "veggente"


# Headers per l'autorizzazione
HEADERS = {
    'Authorization': f'Bot {API_KEY}',
    'Content-Type': 'application/json',
    'Accept-Language': LOCALE
}
USERNAMES_FILE = "Usernames.json"
LOGGING_LEVEL = "INFO"
SKIP_IMAGE_PATH = "skip.jpg"  # L'immagine locale per lo skip

# Configurazioni aggiuntive per il sistema di blacklist
MAX_GROUP_ATTEMPTS = 3  # Numero massimo di tentativi prima del blacklist
BLACKLIST_FILE = "group_blacklist.json"  # File per persistere la blacklist

# Contact information for unblocking
ADMIN_CONTACT = "@sciadouu"