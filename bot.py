# =============================================================================
# IMPORT SECTION
# =============================================================================
# Librerie standard
import asyncio
import logging
import math
import json
import os
from datetime import datetime, timezone
from io import BytesIO
from typing import List, Dict, Any, Optional

# Librerie di terze parti
import requests
import aiohttp
from PIL import Image
import motor.motor_asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Import di Aiogram
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, FSInputFile, Message, ChatMemberUpdated
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command, ChatMemberUpdatedFilter, KICKED, MEMBER, ADMINISTRATOR
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from services.notification_service import EnhancedNotificationService, NotificationType
from services.db_manager import MongoManager
from config import (
    TOKEN,
    WOLVESVILLE_API_KEY,
    CLAN_ID,
    SKIP_IMAGE_PATH,
    BOT_PASSWORD,
    AUTHORIZED_GROUPS,
    OWNER_CHAT_ID,
    ADMIN_NOTIFICATION_CHANNEL,
    ADMIN_IDS,
)
# Import moduli settimana 1 con gestione errori
try:
    from middleware.auth_middleware import GroupAuthorizationMiddleware
    from services.notification_service import NotificationService
    MIDDLEWARE_AVAILABLE = True
except ImportError as e:
    MIDDLEWARE_AVAILABLE = False

    # Import bot_logger con controlli multipli
bot_logger = None
try:
    from utils.logger import bot_logger as imported_bot_logger
    if imported_bot_logger is not None:
        bot_logger = imported_bot_logger
        print("✅ bot_logger importato dalla utils.logger")
    else:
        print("⚠️ bot_logger è None dopo import")
except ImportError as e:
    print(f"❌ ImportError bot_logger: {e}")
    bot_logger = None
# =============================================================================
# CONFIGURAZIONE LOGGING E VARIABILI GLOBALI
# =============================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

LOG_PUBLIC_IP = os.getenv("LOG_PUBLIC_IP", "false").lower() in {"1", "true", "yes", "on"}


def maybe_log_public_ip() -> None:
    """Recupera e registra l'IP pubblico solo quando esplicitamente richiesto."""

    if not LOG_PUBLIC_IP:
        return

    try:
        response = requests.get("https://ifconfig.me/ip", timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Impossibile recuperare l'IP pubblico: %s", exc)
        return

    public_ip = response.text.strip()
    if public_ip:
        logger.info("IP pubblico del bot: %s", public_ip)

# =============================================================================
# CONFIGURAZIONE DI MONGODB
# =============================================================================
MONGO_URI = "mongodb+srv://Admin:X3TaVDKSSQDcfUG@wolvesville.6mrnmcn.mongodb.net/?retryWrites=true&w=majority&appName=Wolvesville"
DATABASE_NAME = "Wolvesville"
USERS_COLLECTION = "users"

mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI, tlsAllowInvalidCertificates=True)
db_manager = MongoManager(mongo_client, DATABASE_NAME)

# Tempo di reset per considerare solo le donazioni future
RESET_TIME = datetime.now(timezone.utc)

# =============================================================================
# MIDDLEWARE PER LOGGING
# =============================================================================
class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            chat_id = event.chat.id
            thread_id = event.message_thread_id if event.is_topic_message else None
            user_id = event.from_user.id
            text = event.text if event.text else "<Non testuale>"
            logger.info(f"Messaggio ricevuto | Chat ID: {chat_id} | Thread ID: {thread_id} | Utente ID: {user_id} | Testo: {text}")
        return await handler(event, data)

class UpdateLoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        logger.info(f"Update RAW => {event}")
        return await handler(event, data)

# =============================================================================
# FUNZIONI DI ACCESSO AL DATABASE
# =============================================================================
# Aggiungi questa funzione dopo le altre funzioni di database

# Aggiungi questa funzione dopo le altre funzioni di database
async def clean_duplicate_users():
    """
    Rimuove utenti duplicati dal database mantenendo solo uno per username.
    """
    try:
        removed_info = await db_manager.remove_duplicate_users()
        total_removed = sum(item.get("removed", 0) for item in removed_info)

        for item in removed_info:
            username = item.get("username", "sconosciuto")
            removed = item.get("removed", 0)
            removed_ids = item.get("removed_ids", [])
            logger.info(
                "Eliminati %s duplicati per %s (documenti rimossi: %s)",
                removed,
                username,
                removed_ids,
            )

        logger.info(
            "Pulizia duplicati completata. Eliminati %s duplicati.",
            total_removed,
        )

    except Exception as e:
        logger.error(f"Errore durante pulizia duplicati: {e}")

async def check_clan_departures():
    """
    Controlla se qualche utente è uscito dal clan e gestisce debiti/pulizia.
    VERSIONE MIGLIORATA con più controlli di sicurezza.
    """
    try:
        # Ottieni membri attuali del clan
        url = f"https://api.wolvesville.com/clans/{CLAN_ID}/members"
        headers = {"Authorization": f"Bot {WOLVESVILLE_API_KEY}", "Accept": "application/json"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    logger.error(f"Errore nel recupero membri clan: {response.status}")
                    return
                current_members = await response.json()

        # Estrai usernames attuali (con validazione)
        current_usernames = set()
        for member in current_members:
            username = member.get("username")
            if username and isinstance(username, str) and len(username) > 0:
                current_usernames.add(username)

        # Ottieni tutti gli utenti nel database
        db_users = await db_manager.list_users()

        users_removed = 0
        debt_notifications = 0

        # Controlla chi è uscito dal clan
        for user in db_users:
            username = user.get("username")
            if not username or username in current_usernames:
                continue  # Utente ancora nel clan o username non valido

            donazioni = user.get("donazioni", {})
            oro = donazioni.get("Oro", 0)
            gem = donazioni.get("Gem", 0)

            # Assicurati che oro e gem siano numeri
            try:
                oro = int(oro) if oro is not None else 0
                gem = int(gem) if gem is not None else 0
            except (ValueError, TypeError):
                oro = gem = 0

            # Se ha debiti (valori negativi)
            if oro < 0 or gem < 0:
                # Invia notifica admin
                debt_message = (
                    f"🚨 <b>USCITA CON DEBITI</b> 🚨\n\n"
                    f"👤 <b>Utente:</b> {username}\n"
                    f"💰 <b>Debito Oro:</b> {abs(oro) if oro < 0 else 0:,}\n"
                    f"💎 <b>Debito Gem:</b> {abs(gem) if gem < 0 else 0:,}\n\n"
                    f"⚠️ L'utente ha abbandonato il clan con debiti non saldati!\n"
                    f"📅 Data controllo: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                )

                # Invia a tutti gli admin
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, debt_message, parse_mode="HTML")
                        debt_notifications += 1
                    except Exception as e:
                        logger.warning(f"Impossibile inviare notifica debito ad admin {admin_id}: {e}")

                logger.info(f"Notificato debito per utente uscito: {username} (Oro: {oro}, Gem: {gem})")

            else:
                # Nessun debito, elimina dal database
                removed = await db_manager.remove_user_by_username(username)
                if removed > 0:
                    users_removed += 1
                    logger.info(f"Utente {username} rimosso dal database (nessun debito)")

        # Log riassuntivo
        logger.info(f"Controllo uscite clan completato: {users_removed} utenti rimossi, {debt_notifications} notifiche debiti inviate")

    except Exception as e:
        logger.error(f"Errore durante controllo uscite clan: {e}")


# MODIFICA la funzione prepopulate_users per evitare duplicati futuri
async def prepopulate_users():
    """
    Pre-popolazione degli utenti: per ogni membro recuperato dall'API del clan,
    crea un record (se non esistente) con bilancio iniziale a 0.
    EVITA DUPLICATI usando username come chiave unica.
    """
    url = f"https://api.wolvesville.com/clans/{CLAN_ID}/members"
    headers = {"Authorization": f"Bot {WOLVESVILLE_API_KEY}", "Accept": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                logger.error(f"Errore nel recupero dei membri: {response.status}")
                return
            members = await response.json()

            for member in members:
                username = member.get("username")
                if username:
                    # UPSERT con controllo duplicati migliorato
                    inserted = await db_manager.ensure_user(username)
                    if inserted:
                        logger.info(f"Utente {username} pre-popolato con bilancio 0.")


async def update_user_balance(username: str, currency: str, amount: int):
    """
    Aggiorna il bilancio di un utente per una determinata valuta.
    Se 'currency' è "gold" (o qualunque forma simile) viene usato il campo "Oro",
    mentre se è "gem" viene usato "Gem". In questo modo si evita di creare campi
    duplicati (es. "Gold") e si mantiene il DB con soli due campi: Oro e Gem.
    """
    normalized_currency = await db_manager.update_user_balance(username, currency, amount)
    logger.info(f"Aggiornato bilancio per {username}: {normalized_currency} += {amount}")


async def process_ledger():
    """
    Recupera il ledger dal clan e aggiorna il DB solo con i record di tipo "DONATE" non ancora processati.
    """
    url = f"https://api.wolvesville.com/clans/{CLAN_ID}/ledger"
    headers = {
        "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
        "Accept": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                logger.error(f"Errore nel recupero del ledger: {response.status}")
                return
            ledger_data = await response.json()

            for record in ledger_data:
                record_id = record.get("id")
                record_type = record.get("type", "")
                # Vogliamo processare solo i record di tipo "DONATE"
                if record_type != "DONATE":
                    continue

                # Verifica se abbiamo già processato questo record
                if await db_manager.has_processed_ledger(record_id):
                    # Significa che l'abbiamo già gestito, quindi skip
                    continue

                # Non ancora processato => aggiorna il bilancio
                username = record.get("playerUsername")
                gold_amount = record.get("gold", 0) or 0
                gems_amount = record.get("gems", 0) or 0

                # Se c'è un username e c'è effettivamente una donazione > 0
                if username and (gold_amount > 0 or gems_amount > 0):
                    # Aggiungi gold a Oro
                    if gold_amount > 0:
                        await update_user_balance(username, "Oro", gold_amount)
                    # Aggiungi gems
                    if gems_amount > 0:
                        await update_user_balance(username, "Gem", gems_amount)

                    await db_manager.log_donation(
                        record_id,
                        username,
                        gold_amount,
                        gems_amount,
                        raw_record=record,
                    )

                # Segna questo record come "già processato"
                await db_manager.mark_ledger_processed(record_id, raw_record=record)

async def process_mission(
    participants: List[str],
    mission_type: str,
    *,
    mission_id: Optional[str] = None,
    outcome: str = "processed",
    source: str = "manual",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Processa una missione, applica i costi e registra la partecipazione nel database."""

    if not participants:
        logger.info("Processo missione %s saltato: nessun partecipante fornito.", mission_type)
        return None

    mission_type = mission_type or "Unknown"
    mission_type_lower = mission_type.lower()
    participant_count = len(participants)
    cost = 0
    currency_key = mission_type

    if mission_type_lower == "gold":
        cost = 500
        currency_key = "Gold"
    elif mission_type_lower == "gem":
        if participant_count > 7:
            cost = 140
        elif 5 <= participant_count <= 7:
            cost = 150
        else:
            cost = 0
        currency_key = "Gem"

    if cost != 0:
        for user in participants:
            await update_user_balance(user, currency_key, -cost)
        logger.info(
            "Applicato costo di %s %s a %s partecipanti (missione %s).",
            cost,
            "Oro" if mission_type_lower == "gold" else "Gem",
            participant_count,
            mission_type,
        )
    else:
        logger.info(
            "Registrata missione %s senza costi aggiuntivi per %s partecipanti.",
            mission_type,
            participant_count,
        )

    metadata_payload = dict(metadata or {})
    metadata_payload.setdefault("participants_count", participant_count)
    metadata_payload.setdefault("cost_applied", cost)

    event_id = await db_manager.log_mission_participation(
        mission_id,
        mission_type,
        participants,
        cost_per_participant=cost,
        outcome=outcome,
        source=source,
        metadata=metadata_payload,
    )

    if event_id:
        logger.info(
            "Registrata partecipazione missione %s (event_id=%s) con %s partecipanti.",
            mission_id or "manual",
            event_id,
            participant_count,
        )

    return event_id

async def process_active_mission_auto():
    """
    Controlla se c'è una missione attiva tramite GET /clans/{CLAN_ID}/quests/active.
    Se la missione è attiva e non ancora processata, sottrae il costo per ogni partecipante
    in base al tipo (Gold=500, Gem=150 o 140) e registra la missione nella collection
    "processed_active_missions" per evitare duplicazioni.
    """
    url = f"https://api.wolvesville.com/clans/{CLAN_ID}/quests/active"
    headers = {"Authorization": f"Bot {WOLVESVILLE_API_KEY}", "Accept": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.error(f"Errore nel recupero della missione attiva: {resp.status}")
                return
            active_data = await resp.json()

    # "quest" e "participants" e "tierStartTime" possono stare a livelli diversi
    quest = active_data.get("quest")
    if not quest:
        logger.info("Nessuna missione attiva trovata.")
        return

    mission_id = quest.get("id")
    tier_start_time = active_data.get("tierStartTime")  # <-- estratto dal top-level
    if not mission_id or not tier_start_time:
        logger.error("Missione attiva priva di id o tierStartTime.")
        return

    if await db_manager.has_processed_active_mission(mission_id):
        logger.info(f"Missione {mission_id} già processata. Nessuna operazione eseguita.")
        return

    # Partecipanti a livello top
    participants = active_data.get("participants", [])
    usernames = [p.get("username") for p in participants if p.get("username")]
    count = len(usernames)
    if not usernames:
        logger.info("Nessun partecipante trovato nella missione attiva.")
        return

    mission_type = "Gem" if quest.get("purchasableWithGems", False) else "Gold"
    if mission_type == "Gold":
        cost = 500
    else:
        if 5 <= count <= 7:
            cost = 150
        elif count > 7:
            cost = 140
        else:
            cost = 0

    for username in usernames:
        await update_user_balance(username, mission_type, -cost)
        logger.info(f"Dedotto {cost} {('Oro' if mission_type=='Gold' else 'Gem')} per {username} nella missione {mission_id}")

    metadata = {
        "tier_start_time": tier_start_time,
        "participant_count": count,
    }

    event_id = await db_manager.log_mission_participation(
        mission_id,
        mission_type,
        usernames,
        cost_per_participant=cost,
        outcome="auto_processed",
        source="active_mission",
        metadata=metadata,
    )

    await db_manager.mark_active_mission_processed(mission_id, tier_start_time)
    logger.info(
        "Missione %s processata e registrata (event_id=%s).",
        mission_id,
        event_id,
    )

# =============================================================================
# FUNZIONI HELPER PER FSM E GESTIONE MESSAGGI DI MODIFICA
# =============================================================================
async def add_modify_msg(state: FSMContext, msg: Message):
    """
    Aggiunge l'ID del messaggio inviato allo state per eventuale cancellazione successiva.
    """
    data = await state.get_data()
    msg_ids = data.get("modify_msg_ids", [])
    msg_ids.append(msg.message_id)
    await state.update_data(modify_msg_ids=msg_ids)

# =============================================================================
# DEFINIZIONE DEGLI STATE PER LA MODIFICA E PER IL PROFILO GIOCATORE
# =============================================================================
class ModifyStates(StatesGroup):
    CHOOSING_PLAYER = State()
    CHOOSING_CURRENCY = State()
    ENTERING_AMOUNT = State()

class PlayerStates(StatesGroup):
    MEMBER_CHECK = State()
    PROFILE_SEARCH = State()

# =============================================================================
# DEFINIZIONE DELLE CALLBACK DATA (per il flusso menu, modifica e missione)
# =============================================================================
class ModifyCallback(CallbackData, prefix="modify"):
    action: str
    value: str = ""

class MenuCallback(CallbackData, prefix="menu"):
    action: str

class MissionCallback(CallbackData, prefix="mission"):
    action: str

# =============================================================================
# FUNZIONI DI PAGINAZIONE DEI GIOCATORI
# NOTA: Le seguenti funzioni "create_players_keyboard" e "make_page_text" sono
# duplicate nel file. Qui ne trovi una versione; più avanti nel file viene
# ripetuta (non rimuovere la duplicazione come richiesto).
# =============================================================================
def is_admin(user_id: int) -> bool:
    """
    Verifica se l'utente è un admin autorizzato.
    """
    return user_id in ADMIN_IDS

async def check_admin_access(callback: types.CallbackQuery) -> bool:
    """
    Controlla se l'utente ha accesso admin e invia messaggio di errore se necessario.
    """
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Non hai le autorizzazioni per questa operazione", show_alert=True)
        return False
    return True

def create_players_keyboard(players: List[str], page: int, page_size: int = 10) -> InlineKeyboardMarkup:
    """
    Crea una tastiera inline per la selezione dei giocatori, con paginazione.
    """
    start_index = page * page_size
    end_index = start_index + page_size
    page_players = players[start_index:end_index]
    kb_buttons = [[InlineKeyboardButton(text=username, callback_data=f"modify_player_{username}")]
                  for username in page_players]
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Indietro", callback_data=f"modify_paginate_{page-1}"))
    if end_index < len(players):
        nav_buttons.append(InlineKeyboardButton(text="➡️ Avanti", callback_data=f"modify_paginate_{page+1}"))
    if nav_buttons:
        kb_buttons.append(nav_buttons)
    kb_buttons.append([InlineKeyboardButton(text="Fine", callback_data="modify_finish")])
    return InlineKeyboardMarkup(inline_keyboard=kb_buttons)

def make_page_text(page: int, players: List[str], page_size: int = 10) -> str:
    """
    Ritorna il testo della pagina corrente con l'elenco dei giocatori.
    """
    total_pages = (len(players) - 1) // page_size + 1
    start_index = page * page_size
    end_index = min(start_index + page_size, len(players))
    page_players = players[start_index:end_index]
    text = f"Pagina {page+1}/{total_pages}:\n" + "\n".join(page_players)
    return text

# =============================================================================
# ALTRE FUNZIONI UTILI
# =============================================================================
def escape_markdown_v2(text: str) -> str:
    """
    Funzione mantenuta per compatibilità (anche se con parse_mode="HTML" non viene più usata).
    """
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    return ''.join(f'\\{c}' if c in special_chars else c for c in text)

def format_player_info(player_info):
    """
    Formattta le informazioni del giocatore in HTML.
    """
    def format_field(value, hidden_text="Nascosto"):
        return hidden_text if value in (-1, None, "N/A") else str(value)

    last_online = player_info.get('lastOnline', 'N/A')
    formatted_last_online = last_online.split("T")[0] if "T" in last_online else last_online

    creation_time = player_info.get('creationTime', 'N/A')
    formatted_creation_time = creation_time.split("T")[0] if "T" in creation_time else creation_time

    clan_id = player_info.get('clanId', 'N/A')
    formatted_clan_id = "Nessuno" if clan_id == "N/A" else clan_id

    game_stats = player_info.get('gameStats', {})

    text_info = (
        f"<b>Informazioni per il giocatore</b> <i>{player_info.get('username', 'N/A')}</i>:\n\n"
        f"<b>ID:</b> {player_info.get('id', 'N/A')}\n"
        f"<b>Messaggio Personale:</b>\n{player_info.get('personalMessage', 'N/A')}\n\n"
        f"<b>Livello:</b> {format_field(player_info.get('level', 'N/A'))}\n"
        f"<b>Stato:</b> {player_info.get('status', 'N/A')}\n"
        f"<b>Ultimo Accesso:</b> {formatted_last_online}\n\n"
        f"<b>Roses:</b>\n"
        f" • Ricevute: {format_field(player_info.get('receivedRosesCount'))}\n"
        f" • Inviate: {format_field(player_info.get('sentRosesCount'))}\n\n"
        f"<b>ID Clan:</b> {formatted_clan_id}\n"
        f"<b>Tempo di Creazione:</b> {formatted_creation_time}\n\n"
        f"<b>Statistiche di Gioco:</b>\n"
        f" • Vittorie Totali: {format_field(game_stats.get('totalWinCount'))}\n"
        f" • Sconfitte Totali: {format_field(game_stats.get('totalLoseCount'))}\n"
        f" • Pareggi Totali: {format_field(game_stats.get('totalTieCount'))}\n"
        f" • Tempo Totale di Gioco (minuti): {format_field(game_stats.get('totalPlayTimeInMinutes'))}\n"
    )
    return text_info

async def get_best_resolution_url(url_base: str) -> str:
    """
    Tenta di ottenere la versione in alta risoluzione di un'immagine (@3x, @2x) se disponibile.
    """
    if not isinstance(url_base, str):
        return ""
    if not url_base.endswith(".png"):
        return url_base

    # Prova @3x
    url_3x = url_base.replace(".png", "@3x.png")
    async with aiohttp.ClientSession() as session:
        async with session.head(url_3x) as resp:
            if resp.status == 200:
                return url_3x

    # Prova @2x
    url_2x = url_base.replace(".png", "@2x.png")
    async with aiohttp.ClientSession() as session:
        async with session.head(url_2x) as resp:
            if resp.status == 200:
                return url_2x

    return url_base

def chunk_list(items, chunk_size=6):
    """
    Divide una lista in "chunk" (sotto-liste) della dimensione specificata.
    """
    for i in range(0, len(items), chunk_size):
        yield items[i:i+chunk_size]

# =============================================================================
# FUNZIONI PER GESTIRE I FILE DI DATI DEI CLAN
# =============================================================================
CLAN_DATA_FILE = "clan_data.json"

def load_saved_clans() -> List[Dict[str, str]]:
    """
    Legge clan_data.json e ritorna la lista di clan salvati.
    """
    if not os.path.exists(CLAN_DATA_FILE):
        return []
    try:
        with open(CLAN_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("clans", [])
    except:
        return []

def save_saved_clans(clans: List[Dict[str, str]]):
    """
    Salva la lista di clan nel file clan_data.json.
    """
    data = {"clans": clans}
    with open(CLAN_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_clan_to_file(clan_id: str, clan_name: str):
    """
    Aggiunge un clan al file dei clan salvati (se non già presente).
    """
    clans = load_saved_clans()
    # Verifica duplicazione: se già presente, non aggiungere
    for c in clans:
        if c["id"] == clan_id:
            return
    clans.append({"id": clan_id, "name": clan_name})
    save_saved_clans(clans)

# =============================================================================
# CONFIGURAZIONE DEL BOT E DEL DISPATCHER
# =============================================================================
bot = Bot(
    token=TOKEN,
    session=AiohttpSession(),
    default=DefaultBotProperties(parse_mode="HTML")

)
dp = Dispatcher(storage=MemoryStorage())

# Inizializzazione servizio notifiche
# Initialize notification service
notification_service = EnhancedNotificationService(
    bot=bot,
    admin_ids=ADMIN_IDS,
    admin_channel_id=ADMIN_NOTIFICATION_CHANNEL,
    owner_id=OWNER_CHAT_ID
)

# Setup logging con handler Telegram
bot_logger.add_telegram_handler(bot, ADMIN_IDS)

# Inizializzazione middleware autorizzazione
auth_middleware = GroupAuthorizationMiddleware(
    authorized_groups=set(AUTHORIZED_GROUPS),
    admin_ids=ADMIN_IDS,
    notification_service=notification_service
)

# Registrazione middleware nell'ordine corretto
# IMPORTANTE: Aggiungi PRIMA dei middleware esistenti
dp.update.middleware(LoggingMiddleware())    # Prima il logging
dp.update.middleware(auth_middleware)        # Poi l'autorizzazione

@dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=KICKED))
async def bot_kicked_from_chat(event: ChatMemberUpdated):
    """
    Handler per quando il bot viene rimosso/kickato da una chat.
    Invia notifica se era un gruppo autorizzato.
    """
    chat_id = event.chat.id
    chat_title = event.chat.title or "Chat Privato"

    logger.info(f"Bot rimosso dalla chat: {chat_id} ({chat_title})")

    # Se era un gruppo autorizzato, invia notifica
    if chat_id in AUTHORIZED_GROUPS:
        message = (
            f"⚠️ **BOT RIMOSSO DA GRUPPO AUTORIZZATO**\n\n"
            f"👥 **Gruppo:** {chat_title}\n"
            f"🆔 **Chat ID:** `{chat_id}`\n\n"
            f"🔄 **Azione:** Verificare se l'uscita è intenzionale"
        )
        await notification_service.send_admin_notification(
            message,
            notification_type=NotificationType.WARNING,
            urgent=True
        )

@dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=MEMBER))
async def bot_added_to_chat(event: ChatMemberUpdated):
    """
    Handler per quando il bot viene aggiunto a una chat.
    Implementa il sistema di blacklist richiesto:
    - Controlla se il gruppo è autorizzato
    - Se non autorizzato, gestisce i tentativi e blacklist
    - Invia notifica solo al proprietario (OWNER_CHAT_ID)
    - Dopo 3 tentativi, inserisce in blacklist
    """
    chat_id = event.chat.id
    chat_title = event.chat.title or "Chat Privato"
    user_id = event.from_user.id if event.from_user else None

    logger.info(f"Bot aggiunto alla chat: {chat_id} ({chat_title})")

    # Controlla se la chat è autorizzata
    if chat_id not in AUTHORIZED_GROUPS:
        # Controlla se il gruppo è in blacklist
        if notification_service.is_group_blacklisted(chat_id):
            logger.info(f"Gruppo {chat_id} in blacklist, uscita immediata")
            try:
                await bot.leave_chat(chat_id)
            except Exception as e:
                logger.error(f"Errore nell'uscire dal gruppo blacklistato {chat_id}: {e}")
            return

        # Gestisce l'aggiunta a gruppo non autorizzato
        await notification_service.handle_unauthorized_group_join(
            chat_id=chat_id,
            chat_title=chat_title,
            user_id=user_id
        )

        # Esce dal gruppo non autorizzato
        try:
            await bot.leave_chat(chat_id)
            logger.info(f"Uscito dal gruppo non autorizzato: {chat_id}")
        except Exception as e:
            logger.error(f"Errore nell'uscire dal gruppo non autorizzato {chat_id}: {e}")
    else:
        # Gruppo autorizzato - invia messaggio di benvenuto
        await notification_service.send_authorized_group_notification(chat_id, chat_title)
        logger.info(f"Bot aggiunto con successo al gruppo autorizzato: {chat_id}")

async def on_startup():
    """
    Funzione chiamata all'avvio del bot.
    Invia notifiche di startup sia al proprietario che al canale admin.
    """
    logger.info("Avvio bot in corso...")

    # Invia notifica di startup (risolve il problema delle notifiche startup)
    await notification_service.send_startup_notification()

    logger.info("Avvio bot completato")

async def check_user_debts_on_exit(user_data: dict):
    """
    Funzione migliorata per controllare i debiti utente all'uscita dal clan.
    Invia notifiche sia agli ADMIN_IDS che al canale admin.
    """
    username = user_data.get("username", "Sconosciuto")
    donazioni = user_data.get("donazioni", {})
    oro = donazioni.get("Oro", 0)
    gem = donazioni.get("Gem", 0)

    # Controlla se l'utente ha debiti (bilanci negativi)
    has_debts = oro < 0 or gem < 0

    if has_debts:
        debt_info = {
            "oro": abs(oro) if oro < 0 else 0,
            "gem": abs(gem) if gem < 0 else 0
        }

        # Invia notifica debiti (risolve il problema delle notifiche debiti)
        await notification_service.send_debt_notification(user_data, debt_info)

        logger.info(f"Notifica debiti inviata per l'utente {username}")
# =============================================================================
# VARIABILI GLOBALI PER CHAT E TOPIC
# =============================================================================
CHAT_ID = -1002383442316  # Sostituisci con l'ID del gruppo desiderato
TOPIC_ID = 4            # Sostituisci con l'ID del topic specifico
ADMIN_IDS = [7020291568]
# =============================================================================
# CONFIGURAZIONE DELLO SCHEDULER
# =============================================================================
def setup_scheduler():
    """
    Configura lo scheduler per eseguire periodicamente alcune funzioni.
    """
    scheduler = AsyncIOScheduler(timezone="Europe/Rome")

    # CORREZIONE: Invio skin/messaggi lunedì alle 8:00 (non 11:00)
    scheduler.add_job(
        scheduled_mission_skin,
        "cron",
        day_of_week="mon",
        hour=8,  # Cambiato da 11 a 8
        minute=0,
        timezone="Europe/Rome"
    )
    scheduler.add_job(process_ledger, "interval", minutes=5, next_run_time=datetime.now())
    scheduler.add_job(process_active_mission_auto, "interval", minutes=5, next_run_time=datetime.now())
    scheduler.add_job(prepopulate_users, "interval", days=3, next_run_time=datetime.now())

    # NUOVE funzioni per pulizia e controllo
    scheduler.add_job(clean_duplicate_users, "interval", hours=24, next_run_time=datetime.now())  # Ogni giorno
    scheduler.add_job(check_clan_departures, "interval", hours=6, next_run_time=datetime.now())   # Ogni 6 ore
    # NUOVO: Controllo reminder calendario
    #scheduler.add_job(calendar_service.check_pending_reminders, "interval", minutes=1)

    # NUOVO: Pulizia log settimanale
    #scheduler.add_job(cleanup_old_logs, "cron", day_of_week="sun", hour=2, minute=0)

    scheduler.start()
    logger.info("Scheduler configurato con tutte le funzioni di manutenzione.")

# =============================================================================
# FUNZIONI VARIE DI SUPPORTO
# =============================================================================

async def send_photo_and_log(
    chat_id: int,
    photo: types.BufferedInputFile,
    caption: str = "",
    message_thread_id: int = None
):
    """
    Invia una foto e registra il log dell'operazione.
    """
    logger.info(f"Invio foto a chat_id={chat_id}, didascalia={caption}")
    await bot.send_photo(
        chat_id=chat_id,
        photo=photo,
        caption=caption,
        message_thread_id=message_thread_id
    )

# =============================================================================
# GESTIONE DELLE SKIN E DELLE MISSIONI
# =============================================================================
async def missione_flow(message: types.Message, state: FSMContext):
    """
    Gestisce il flusso delle missioni, mostrando le opzioni per Skin e Skip.
    """
    text = "Missioni"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Skin", callback_data=MissionCallback(action="skin").pack()),
            InlineKeyboardButton(text="Skip", callback_data=MissionCallback(action="skip").pack()),
        ]
    ])
    await message.answer(text, reply_markup=kb)

@dp.callback_query(MissionCallback.filter())
async def handle_mission_callback(callback: types.CallbackQuery, callback_data: MissionCallback, state: FSMContext):
    """
    Gestisce il callback per le missioni:
      - Se "skip": richiede di saltare il tempo di attesa della missione attiva.
      - Se "skin": recupera ed invia le skin disponibili.
    """
    try:
        await callback.message.delete()
    except:
        pass

    # VERSIONE CORRETTA del tuo codice skip
    if callback_data.action == "skip":
        url = f"https://api.wolvesville.com/clans/{CLAN_ID}/quests/active/skipWaitingTime"

        user_id = callback.from_user.id
        username = callback.from_user.username or callback.from_user.first_name
        logger.info(f"Skip command requested by user {user_id} ({username})")

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }

                logger.info(f"Making skip API call to: {url}")

                async with session.post(url, headers=headers) as resp:
                    response_text = await resp.text()
                    logger.info(f"Skip API response: Status {resp.status}, Response: {response_text}")

                    if resp.status == 200:
                        # SUCCESSO - Invia immagine skip
                        try:
                            if os.path.exists(SKIP_IMAGE_PATH):
                                # CORREZIONE PRINCIPALE: FSInputFile invece di InputFile
                                photo = FSInputFile(SKIP_IMAGE_PATH)

                                await callback.message.answer_photo(
                                    photo=photo,
                                    caption="⏰ **Tempo saltato con successo!** 🚀\n\n"
                                           "Tornare a farmare piccoli vermi!! 🐛\n\n"
                                           "_La missione è stata completata automaticamente._"
                                )
                                logger.info(f"Skip successful - Image sent: {SKIP_IMAGE_PATH}")
                            else:
                                # File non esiste - Solo testo
                                await callback.message.answer(
                                    "⏰ **Tempo saltato con successo!** 🚀\n\n"
                                    "Tornare a farmare piccoli vermi!! 🐛"
                                )
                                logger.warning(f"Skip image not found: {SKIP_IMAGE_PATH}")

                        except Exception as img_error:
                            # Errore invio immagine - Fallback a testo
                            logger.error(f"Error sending skip image: {img_error}")
                            await callback.message.answer(
                                "⏰ **Tempo saltato con successo!** 🚀\n\n"
                                "Tornare a farmare piccoli vermi!! 🐛\n"
                                "_(Immagine non disponibile)_"
                            )

                    elif resp.status == 400:
                        # Bad request - Nessuna missione attiva
                        await callback.message.answer(
                            "❌ **Impossibile saltare il tempo**\n\n"
                            "• Nessuna missione attiva\n"
                            "• Missione già completata\n"
                            "• Tempo di attesa già scaduto"
                        )
                        logger.warning(f"Skip failed - No active mission (400): {response_text}")

                    elif resp.status == 401:
                        # Unauthorized
                        await callback.message.answer(
                            "❌ **Errore di autorizzazione**\n\n"
                            "Il bot non ha i permessi per saltare il tempo."
                        )
                        logger.error(f"Skip failed - Unauthorized (401): {response_text}")

                    elif resp.status == 404:
                        # Not found
                        await callback.message.answer(
                            "❌ **Clan o missione non trovati**\n\n"
                            "Verifica la configurazione del clan."
                        )
                        logger.error(f"Skip failed - Not found (404): {response_text}")

                    else:
                        # Altri errori HTTP
                        await callback.message.answer(
                            f"❌ **Errore durante lo skip**\n\n"
                            f"Codice: {resp.status}\n"
                            "Riprova più tardi."
                        )
                        logger.error(f"Skip failed - HTTP {resp.status}: {response_text}")

        except aiohttp.ClientError as network_error:
            # Errori di rete
            logger.error(f"Network error during skip: {network_error}")
            await callback.message.answer(
                "❌ **Errore di connessione**\n\n"
                "Impossibile contattare il server. Riprova più tardi."
            )

        except Exception as e:
            # Altri errori imprevisti
            logger.error(f"Unexpected error during skip: {e}")
            await callback.message.answer(
                "❌ **Errore imprevisto durante lo skip**\n\n"
                "Controlla i log o riprova più tardi."
            )


    else:
        url = f"https://api.wolvesville.com/clans/{CLAN_ID}/quests/available"
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                    "Content-Type": "application/json",
                    "Accept": "application/json"
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
                    try:
                        async with session.get(promo_url) as r_img:
                            if r_img.status == 200:
                                raw = await r_img.read()
                                input_file = types.BufferedInputFile(raw, filename="skin.png")
                                await send_photo_and_log(
                                    chat_id=callback.message.chat.id,
                                    photo=input_file,
                                    caption=caption,
                                    message_thread_id=callback.message.message_thread_id
                                )
                    except Exception as e:
                        logger.warning(f"Impossibile inviare {promo_url}: {e}")

        except Exception as e:
            logger.error(f"Errore missione Skin: {e}")
            await callback.message.answer("Impossibile recuperare le skin!")

async def scheduled_mission_skin():
    """
    Funzione schedulata per inviare automaticamente le skin nella chat.
    """
    url = f"https://api.wolvesville.com/clans/{CLAN_ID}/quests/available"
    try:
        announcement_message = (
            "🌞 Buongiorno Ragazzi e Ragazze!\n\n"
            "Qui il bot ad avvisarvi che oggi è **Lunedì**!!\n\n"
            "Giornata peggiore, ma per fortuna ci sono nuove missioni.\n"
            "Quindi andate a **votare**! 🗳️🔥"
        )

        # Invia messaggio nel gruppo
        await bot.send_message(chat_id=CHAT_ID, text=announcement_message, message_thread_id=TOPIC_ID)

        # Invia annuncio nel gioco
        url_announcement = f"https://api.wolvesville.com/clans/{CLAN_ID}/announcements"
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            payload = {"message": announcement_message}
            async with session.post(url_announcement, headers=headers, json=payload) as resp:
                if resp.status in [200, 201, 204]:
                    logger.info("Annuncio inviato con successo nel gioco!")
                else:
                    response_text = await resp.text()
                    logger.error(f"Errore nell'invio dell'annuncio: {response_text} (Codice: {resp.status})")

        # CORREZIONE: Invia le skin con variabile corretta
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            resp = await session.get(url, headers=headers)
            if resp.status != 200:
                logger.error("Errore nel recupero delle skin programmate")
                return
            data = await resp.json()
            if not data:
                logger.info("Nessuna skin disponibile per l'invio automatico")
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

                try:
                    async with session.get(promo_url) as r_img:
                        if r_img.status == 200:
                            raw = await r_img.read()
                            # FIX: Correggi la variabile
                            skin_file = types.BufferedInputFile(raw, filename="skin.png")
                            await bot.send_photo(
                                chat_id=CHAT_ID,
                                photo=skin_file,  # CORREZIONE QUI
                                caption=caption,
                                message_thread_id=TOPIC_ID
                            )
                except Exception as e:
                    logger.warning(f"Impossibile inviare {promo_url}: {e}")

    except Exception as e:
        logger.error(f"Errore nell'invio automatico delle skin: {e}")

# Nuovo helper: send_and_log (se non già definito)
async def send_and_log(text: str, chat_id: int, reply_markup: types.InlineKeyboardMarkup = None):
    logger.info(f"Sending message to {chat_id}: {text}")
    return await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

# =============================================================================
# COMANDO /START
# =============================================================================
@dp.message(Command("start"))
async def start_command(message: types.Message):
    """
    Comando /start che mostra il menu principale.
    I pulsanti e le relative callback sono:
      - "Giocatore" => "menu_player"
      - "Clan" => "menu_clan"
      - "Missione" => "menu_missione" (richiama missione_flow, che mostra le skin e consente di skip)
      - "Help" => "menu_help"
      - "Bilancio" => "menu_balances" (richiama show_balances)
      - "Player Missione" => "menu_partecipanti" (richiama partecipanti_command)
    """
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Giocatore", callback_data="menu_player")],
        [InlineKeyboardButton(text="🏰 Clan", callback_data="menu_clan")],
        [InlineKeyboardButton(text="⏩ Missione", callback_data="menu_missione")],
        [InlineKeyboardButton(text="❓ Help", callback_data="menu_help")],
        [InlineKeyboardButton(text="Bilancio", callback_data="menu_balances"),
         InlineKeyboardButton(text="Player Missione", callback_data="menu_partecipanti")]
    ])
    await send_and_log("Scegli un'opzione:", message.chat.id, reply_markup=kb)
    try:
        if message.chat.type != 'private':
            bot_member = await message.chat.get_member(message.bot.id)
            if bot_member.can_delete_messages:
                await message.delete()
        else:
            await message.delete()
    except Exception as e:
        logger.warning(f"Cannot delete message: {e}")

# =============================================================================
# COMANDO /MENU
# =============================================================================
@dp.message(Command("menu"))
async def menu_command(message: types.Message):
    """
    Comando /menu che mostra il menu principale.
    I pulsanti sono gli stessi di /start.
    """
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Giocatore", callback_data="menu_player")],
        [InlineKeyboardButton(text="🏰 Clan", callback_data="menu_clan")],
        [InlineKeyboardButton(text="⏩ Missione", callback_data="menu_missione")],
        [InlineKeyboardButton(text="❓ Help", callback_data="menu_help")],
        [InlineKeyboardButton(text="Bilancio", callback_data="menu_balances"),
         InlineKeyboardButton(text="Player Missione", callback_data="menu_partecipanti")]
    ])
    await message.answer("Scegli un'opzione:", reply_markup=kb)
    try:
        if message.chat.type != 'private':
            bot_member = await message.chat.get_member(message.bot.id)
            if bot_member.can_delete_messages:
                await message.delete()
        else:
            await message.delete()
    except Exception as e:
        logger.warning(f"Cannot delete message: {e}")

# =============================================================================
# GESTIONE DEI CALLBACK DEL MENU
# =============================================================================
@dp.callback_query(lambda c: c.data and c.data.startswith("menu_"))
async def handle_menu_callback(callback: types.CallbackQuery, state: FSMContext):
    """
    Gestisce i callback dal menu principale.
    Le scelte supportate sono:
      - "player": richiama il flusso per la verifica se è membro (es. con un messaggio "È un membro del clan?")
      - "clan": richiama la funzione clan_flow (già implementata altrove)
      - "missione": richiama missione_flow (la tua implementazione preesistente)
      - "balances": richiama show_balances per visualizzare i bilanci
      - "partecipanti": richiama partecipanti_command per abilitare i player alla missione
      - "help": mostra il testo di aiuto
    """
    choice = callback.data.split("_", 1)[-1]
    logger.info(f"Menu callback choice: {choice}")
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Error deleting message: {e}")

    if choice == "player":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Sì", callback_data="is_member_yes"),
                InlineKeyboardButton(text="❌ No", callback_data="is_member_no")
            ]
        ])
        await callback.message.answer("È un membro del clan?", reply_markup=keyboard)
        await state.set_state(PlayerStates.MEMBER_CHECK)
    elif choice == "clan":
        # Chiamata alla funzione clan_flow (già presente)
        await clan_flow(callback.message, state)
    elif choice == "missione":
        # Chiamata alla funzione missione_flow (già presente)
        await missione_flow(callback.message, state)
    elif choice == "balances":
        await show_balances(callback.message)
    elif choice == "partecipanti":
        # Chiamata al flusso per abilitare i partecipanti (equivalente a /partecipanti)
        await partecipanti_command(callback.message, state)
    elif choice == "help":
        help_text = """<b>🤖 GUIDA COMPLETA BOT CLAN</b>

    <b>📋 FUNZIONI PRINCIPALI</b>

    <b>👤 GIOCATORE</b>
    🔸 <i>Membro del Clan</i>: Visualizza lista paginata di tutti i membri
    🔸 <i>Ricerca Esterna</i>: Cerca qualsiasi giocatore per username
    🔸 <i>Profili Completi</i>: Statistiche, livello, clan, avatar
    🔸 <i>Avatar Gallery</i>: Visualizza tutti gli avatar del giocatore

    <b>🏰 CLAN</b>
    🔸 <i>Clan Salvati</i>: Lista dei clan già cercati
    🔸 <i>Ricerca Diretta</i>: <code>/clan [ID]</code> per nuove ricerche
    🔸 <i>Info Complete</i>: Membri, risorse, statistiche clan

    <b>⚔️ MISSIONI</b>
    🔸 <i>Skin Disponibili</i>: Visualizza missioni con anteprime
    🔸 <i>Skip Timer</i>: Salta tempo di attesa (solo admin)
    🔸 <i>Invio Automatico</i>: Ogni lunedì alle 11:00

    <b>💰 BILANCIO DONAZIONI</b>
    🔸 <i>Calcolo Automatico</i>: Traccia donazioni e costi missioni
    🔸 <i>Visualizzazione</i>: Tabella ordinata di tutti i bilanci
    🔸 <i>Modifica Admin</i>: Solo amministratori possono modificare
    🔸 <i>Gestione Debiti</i>: Notifiche automatiche per uscite con debiti

    <b>🎯 ABILITAZIONE MISSIONI</b>
    🔸 <i>Voti Automatici</i>: Abilita chi ha votato per una missione
    🔸 <i>Gestione Partecipanti</i>: Controllo completo dei partecipanti

    <b>🔧 FUNZIONI ADMIN</b>
    🔸 <i>Pulizia Database</i>: <code>/cleanup</code> - Rimuove duplicati
    🔸 <i>Controllo Uscite</i>: Monitora membri usciti dal clan
    🔸 <i>Gestione Automatica</i>: Sistema scheduler per manutenzione

    <b>🔄 AUTOMAZIONI</b>
    🔸 <i>Ledger Donazioni</i>: Aggiornamento ogni 5 minuti
    🔸 <i>Missioni Attive</i>: Calcolo costi ogni 5 minuti
    🔸 <i>Membri Clan</i>: Sincronizzazione ogni 3 giorni
    🔸 <i>Pulizia DB</i>: Rimozione duplicati ogni 24 ore
    🔸 <i>Controllo Uscite</i>: Verifica debiti ogni 6 ore

    <b>💡 SUGGERIMENTI</b>
    • Usa <code>/start</code> o <code>/menu</code> per navigare
    • I comandi admin richiedono autorizzazione
    • Le modifiche ai bilanci sono tracciate automaticamente
    • Il bot mantiene cronologia delle ricerche clan"""
        await callback.message.answer(help_text, parse_mode="HTML")
    else:
        await callback.message.answer("Opzione non riconosciuta.")


@dp.message(Command("cleanup"))
async def manual_cleanup(message: types.Message):
    """
    Comando manuale per admin per pulire duplicati e controllare uscite clan.
    """
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Non hai i permessi per questo comando.")
        return

    try:
        loading_msg = await message.answer("🔄 Avvio pulizia database...")

        await clean_duplicate_users()
        await check_clan_departures()

        await loading_msg.edit_text("✅ Pulizia completata!\n\n🗂️ Duplicati rimossi\n👥 Controllo uscite clan eseguito")

    except Exception as e:
        logger.error(f"Errore cleanup manuale: {e}")
        await message.answer("❌ Errore durante la pulizia. Controlla i log.")


# =============================================================================
# NUOVO FLUSSO PER /partecipanti E ABILITAZIONE
# =============================================================================

# Definizione degli stati per questo flusso
class MissionStates(StatesGroup):
    SELECTING_MISSION = State()
    CONFIRMING_PARTICIPANTS = State()

async def get_available_missions() -> List[Dict]:
    """
    Recupera le missioni disponibili tramite GET /quests/available.
    """
    url = f"https://api.wolvesville.com/clans/{CLAN_ID}/quests/available"
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bot {WOLVESVILLE_API_KEY}", "Accept": "application/json"}
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                missions = await resp.json()
                return missions
            else:
                logger.error(f"Errore nel recupero delle missioni: status {resp.status}")
                return []

async def get_clan_member_ids(session: Optional[aiohttp.ClientSession] = None) -> List[str]:
    """Recupera gli ID di tutti i membri del clan per gestire la partecipazione alle missioni."""

    close_session = False
    members: List[Dict[str, Any]] = []

    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    try:
        url = f"https://api.wolvesville.com/clans/{CLAN_ID}/members"
        headers = {
            "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
            "Accept": "application/json",
        }
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                error_body = await resp.text()
                logger.error(
                    "Errore nel recupero dei membri del clan: status %s, risposta %s",
                    resp.status,
                    error_body,
                )
                return []

            data = await resp.json()
            if isinstance(data, list):
                members = data
            elif isinstance(data, dict):
                members_value = data.get("members", [])
                if isinstance(members_value, list):
                    members = members_value
                else:
                    logger.error("Formato inatteso nella risposta dei membri del clan: %s", data)
                    return []
            else:
                logger.error("Formato inatteso nella risposta dei membri del clan: %s", data)
                return []
    except Exception as exc:
        logger.error("Eccezione durante il recupero dei membri del clan: %s", exc)
        return []
    finally:
        if close_session:
            await session.close()

    member_ids: List[str] = []
    for member in members:
        if not isinstance(member, dict):
            continue

        member_id = (
            member.get("playerId")
            or member.get("id")
            or member.get("memberId")
            or member.get("userId")
        )

        if not member_id:
            player_data = member.get("player")
            if isinstance(player_data, dict):
                member_id = (
                    player_data.get("playerId")
                    or player_data.get("id")
                    or player_data.get("userId")
                )

        if member_id:
            member_ids.append(str(member_id))
        else:
            logger.warning("Impossibile determinare l'ID per il membro: %s", member)

    unique_member_ids = list(dict.fromkeys(member_ids))
    if not unique_member_ids:
        logger.warning("Nessun ID valido trovato nella lista dei membri del clan.")

    return unique_member_ids



@dp.message(Command("partecipanti"))
async def partecipanti_command(message: types.Message, state: FSMContext):
    """
    Avvia il flusso per abilitare i partecipanti in una missione.
    1. Ottiene le missioni disponibili.
    2. Mostra una tastiera con un pulsante per ciascuna missione (utilizzando il nome estratto dal campo promoImageUrl).
    3. Imposta lo stato in SELECTING_MISSION.
    """
    missions = await get_available_missions()
    if not missions:
        await message.answer("Nessuna missione disponibile al momento.")
        return
    buttons = []
    for mission in missions:
        promo_url = mission.get("promoImageUrl", "")
        name = "Sconosciuto"
        if promo_url:
            filename = promo_url.split("/")[-1]
            name = filename.split(".")[0]
        mission_id = mission.get("id")
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"mission_select_{mission_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Per quale missione si intende abilitare i partecipanti?", reply_markup=kb)
    await state.update_data(available_missions=missions)
    await state.set_state(MissionStates.SELECTING_MISSION)

@dp.callback_query(lambda c: c.data and c.data.startswith("mission_select_"))
async def mission_select_callback(callback: types.CallbackQuery, state: FSMContext):
    """
    Dopo che l'utente seleziona una missione:
    1. Cancella il messaggio dei pulsanti.
    2. Richiama GET /quests/votes per ottenere i voti e filtra quelli della missione selezionata.
    3. Salva la lista dei playerId (voti) nello state.
    4. Mostra una tastiera con "Si" / "No" per confermare l'abilitazione.
    """
    selected_mission_id = callback.data.split("mission_select_")[-1]
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Errore nella cancellazione del messaggio: {e}")
    votes_url = f"https://api.wolvesville.com/clans/{CLAN_ID}/quests/votes"
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bot {WOLVESVILLE_API_KEY}", "Accept": "application/json"}
        async with session.get(votes_url, headers=headers) as resp:
            if resp.status != 200:
                await callback.message.answer("Impossibile recuperare i voti.")
                return
            votes_data = await resp.json()
    # Supponiamo che i voti siano strutturati in un dizionario "votes" dove la chiave è l'id della missione
    votes_dict = votes_data.get("votes", {})
    mission_player_ids = votes_dict.get(selected_mission_id, [])
    logger.info(f"Numero di voti per missione {selected_mission_id}: {len(mission_player_ids)}")
    await state.update_data(selected_mission_id=selected_mission_id, mission_player_ids=mission_player_ids)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Si", callback_data=f"enable_votes_yes_{selected_mission_id}"),
         InlineKeyboardButton(text="No", callback_data=f"enable_votes_no_{selected_mission_id}")]
    ])
    await callback.message.answer("Vuoi abilitare i partecipanti che hanno votato per questa missione?", reply_markup=kb)
    await state.set_state(MissionStates.CONFIRMING_PARTICIPANTS)

@dp.callback_query(lambda c: c.data and c.data.startswith("enable_votes_"))
async def enable_votes_callback(callback: types.CallbackQuery, state: FSMContext):
    """
    Se l'utente conferma ("Si"), per ogni playerId salvato nello state viene inviata una richiesta PUT
    per abilitare la partecipazione, con il payload {"participateInQuests": True}.
    """
    parts = callback.data.split("_")
    decision = parts[2]  # "yes" o "no"
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Errore nella cancellazione del messaggio: {e}")
    if decision == "yes":
        data = await state.get_data()
        mission_player_ids_raw = data.get("mission_player_ids", [])
        selected_mission_id = data.get("selected_mission_id")

        mission_player_ids = [str(pid) for pid in mission_player_ids_raw]
        mission_player_ids = list(dict.fromkeys(mission_player_ids))
        logger.info(
            "Abilitazione - numero di player unici con voto per la missione %s: %s",
            selected_mission_id,
            len(mission_player_ids),
        )
        if not mission_player_ids:
            await callback.message.answer("Nessun player ha votato per questa missione.")
            await state.clear()
            return
        if not selected_mission_id:
            logger.error("Missione selezionata non trovata nello state dell'FSM.")
            await callback.message.answer(
                "Impossibile determinare la missione selezionata. Ripeti l'operazione."
            )
            await state.clear()
            return

        disable_failures: List[str] = []
        enable_failures: List[str] = []
        warning_messages: List[str] = []

        try:
            async with aiohttp.ClientSession() as session:
                all_member_ids = await get_clan_member_ids(session)
                json_headers = {
                    "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                    "Content-Type": "application/json",
                }

                if all_member_ids:
                    disable_payload = {"participateInQuests": False}
                    for member_id in all_member_ids:
                        url_put_disable = (
                            f"https://api.wolvesville.com/clans/{CLAN_ID}/members/{member_id}/participateInQuests"
                        )
                        async with session.put(
                            url_put_disable, headers=json_headers, json=disable_payload
                        ) as resp:
                            response_text = await resp.text()
                            logger.info(
                                "PUT %s -> %s, %s",
                                url_put_disable,
                                resp.status,
                                response_text,
                            )
                            if resp.status not in [200, 201, 204]:
                                disable_failures.append(str(member_id))
                                logger.error(
                                    "Errore nella disattivazione del membro %s: status %s, risposta %s",
                                    member_id,
                                    resp.status,
                                    response_text,
                                )
                else:
                    warning_messages.append(
                        "⚠️ Impossibile recuperare la lista completa dei membri, salto la disattivazione preventiva."
                    )
                    logger.warning(
                        "Lista membri vuota durante la disattivazione preventiva dei partecipanti alla missione."
                    )

                enable_payload = {"participateInQuests": True}
                for pid in mission_player_ids:
                    url_put_enable = (
                        f"https://api.wolvesville.com/clans/{CLAN_ID}/members/{pid}/participateInQuests"
                    )
                    async with session.put(
                        url_put_enable, headers=json_headers, json=enable_payload
                    ) as resp:
                        response_text = await resp.text()
                        logger.info(
                            "PUT %s -> %s, %s",
                            url_put_enable,
                            resp.status,
                            response_text,
                        )
                        if resp.status not in [200, 201, 204]:
                            enable_failures.append(str(pid))
                            logger.error(
                                "Errore nell'abilitazione del membro %s: status %s, risposta %s",
                                pid,
                                resp.status,
                                response_text,
                            )

                await callback.message.answer("I partecipanti che hanno votato sono stati abilitati.")

                claim_url = f"https://api.wolvesville.com/clans/{CLAN_ID}/quests/claim"
                claim_headers = {
                    "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                claim_payload = {"questId": selected_mission_id}

                async with session.post(
                    claim_url, headers=claim_headers, json=claim_payload
                ) as resp:
                    claim_body = await resp.text()
                    if resp.status in [200, 201, 204]:
                        await callback.message.answer("🚀 Missione avviata con successo.")
                        logger.info(
                            "Missione %s avviata con successo: %s",
                            selected_mission_id,
                            claim_body,
                        )
                    else:
                        logger.error(
                            "Errore nell'avvio della missione %s: status %s, risposta %s",
                            selected_mission_id,
                            resp.status,
                            claim_body,
                        )
                        await callback.message.answer(
                            f"⚠️ Impossibile avviare la missione (status {resp.status})."
                        )

                for message_text in warning_messages:
                    await callback.message.answer(message_text)

                if disable_failures:
                    await callback.message.answer(
                        f"⚠️ Disattivazione non riuscita per {len(disable_failures)} membri. Controlla i log per i dettagli."
                    )

                if enable_failures:
                    await callback.message.answer(
                        f"⚠️ Abilitazione non riuscita per {len(enable_failures)} partecipanti. Controlla i log per i dettagli."
                    )
        except Exception as exc:
            logger.error(
                "Errore durante la gestione dell'abilitazione missione per %s: %s",
                selected_mission_id,
                exc,
            )
            await callback.message.answer(
                "Si è verificato un errore durante l'abilitazione dei partecipanti. Riprova più tardi."
            )
    else:
        await callback.message.answer("Abilitazione annullata.")
    await state.clear()


"""BILANCI """
@dp.message(Command("balances"))
async def show_balances(message: types.Message):
    users = await db_manager.list_users()
    logger.info("Sto per costruire la tabella bilanci. Ecco i documenti dal DB:")
    for doc in users:
        logger.info(f"Doc utente: {doc}")
    lines = []
    lines.append("Utente           Oro     Gem")
    lines.append("-----------------------------")

    for doc in users:
        username = doc.get("username", "Sconosciuto")
        donazioni = doc.get("donazioni", {})
        oro = donazioni.get("Oro", 0)
        gem = donazioni.get("Gem", 0)
        # Allineiamo a sinistra con una larghezza fissa, ad esempio 15 caratteri
        lines.append(f"{username:<15}{oro:<8}{gem}")

    text = (
        "<b>Bilanci Donazioni</b>\n\n"
        "<pre>\n" + "\n".join(lines) + "\n</pre>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Modifica", callback_data="modify_start")],
        [InlineKeyboardButton(text="Chiudi", callback_data="close_balances")]
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(Command("clan"))
async def clan_command(message: types.Message):
    try:
        # Dividi il comando in parti
        args = message.text.strip().split()

        # Controlla che ci sia il parametro clan_id
        if len(args) < 2:
            await message.answer("❌ Specifica l'ID del clan.\n📝 Uso: /clan <clanId>\n📋 Esempio: /clan 12345")
            return

        clan_id = args[1].strip()

        # Validazione formato clan_id (deve essere alfanumerico e di lunghezza ragionevole)
        if not clan_id:
            await message.answer("❌ ID clan non può essere vuoto.")
            return

        if len(clan_id) < 5 or len(clan_id) > 50:
            await message.answer("❌ ID clan deve essere tra 5 e 50 caratteri.")
            return

        # Controlla che contenga solo caratteri validi (lettere, numeri, trattini)
        if not all(c.isalnum() or c in '-_' for c in clan_id):
            await message.answer("❌ ID clan può contenere solo lettere, numeri, trattini e underscore.")
            return

        # Resto del codice originale...
        url = f"https://api.wolvesville.com/clans/{clan_id}/info"
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                    "Accept": "application/json"
                }
                resp = await session.get(url, headers=headers)
                if resp.status != 200:
                    await message.answer("❌ Clan non trovato. Verifica l'ID e riprova.")
                    return
                clan_info = await resp.json()

            clan_name = clan_info.get("name", "Sconosciuto")
            add_clan_to_file(clan_id, clan_name)

            response_text = (
                f"<b>Informazioni sul Clan:</b>\n"
                f"ID: {clan_id}\n"
                f"Nome: {clan_name}\n"
                f"Descrizione: {clan_info.get('description', 'N/A')}\n"
                f"XP: {str(clan_info.get('xp', 'N/A'))}\n"
                f"Lingua: {clan_info.get('language', 'N/A')}\n"
                f"Tag: {clan_info.get('tag', 'N/A')}\n"
                f"Tipo di Unione: {clan_info.get('joinType', 'N/A')}\n"
                f"ID Leader: {clan_info.get('leaderId', 'N/A')}\n"
                f"Conteggio quest: {str(clan_info.get('questHistoryCount', 'N/A'))}\n"
                f"Livello Minimo: {str(clan_info.get('minLevel', 'N/A'))}\n"
                f"Membri: {str(clan_info.get('memberCount', 'N/A'))}\n"
                f"Oro: {str(clan_info.get('gold', 'N/A'))}\n"
                f"Gemme: {str(clan_info.get('gems', 'N/A'))}\n"
            )
            await message.answer(response_text)

        except aiohttp.ClientError as e:
            logger.error(f"Errore di rete /clan {clan_id} => {e}")
            await message.answer("❌ Errore di connessione. Riprova più tardi.")
        except Exception as e:
            logger.error(f"Errore generico /clan {clan_id} => {e}")
            await message.answer("❌ Si è verificato un errore. Riprova più tardi.")

    except Exception as e:
        logger.error(f"Errore critico in clan_command: {e}")
        await message.answer("❌ Errore interno del bot. Riprova più tardi.")

# ===========================
# Gestione "Clan" dal menu => "Vuoi visualizzare i clan salvati?" => Sì/No
# ===========================
async def clan_flow(message: types.Message, state: FSMContext):
    text = "Ciao. Vuoi visualizzare i clan salvati?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Sì", callback_data="clan_si"),
            InlineKeyboardButton(text="No", callback_data="clan_no")
        ]
    ])
    await message.answer(text, reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith("clan_"))
async def handle_clan_callback(callback: types.CallbackQuery, state: FSMContext):
    # Split into exactly 3 parts to properly handle "clan_show_<id>"
    parts = callback.data.split("_", 2)

    try:
        await callback.message.delete()
    except:
        pass

    # If it's a show command (clan_show_<id>)
    if len(parts) == 3 and parts[1] == "show":
        clan_id = parts[2]
        url = f"https://api.wolvesville.com/clans/{clan_id}/info"

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                    "Accept": "application/json"
                }

                resp = await session.get(url, headers=headers)
                if resp.status != 200:
                    await callback.message.answer("Impossibile recuperare le info del clan!")
                    return

                clan_info = await resp.json()

            clan_name = clan_info.get("name", "Sconosciuto")

            response_text = (
                f"<b>Informazioni sul Clan</b>\n\n"
                f"<b>Nome:</b> {clan_name}\n"
                f"<b>Descrizione:</b> {clan_info.get('description', 'N/A')}\n"
                f"<b>XP:</b> {clan_info.get('xp', 'N/A')}\n"
                f"<b>Lingua:</b> {clan_info.get('language', 'N/A')}\n"
                f"<b>Tag:</b> {clan_info.get('tag', 'N/A')}\n"
                f"<b>Membri:</b> {clan_info.get('memberCount', 'N/A')}\n"
                f"<b>Oro:</b> {clan_info.get('gold', 'N/A')}\n"
                f"<b>Gemme:</b> {clan_info.get('gems', 'N/A')}"
            )

            await callback.message.answer(response_text, parse_mode="HTML")

        except Exception as e:
            logger.error(f"Errore show clan salvato {clan_id}: {e}")
            await callback.message.answer("Errore durante la ricerca del clan salvato.")

    # If it's si/no command
    elif parts[1] == "si":
        saved_clans = load_saved_clans()

        if not saved_clans:
            await callback.message.answer("Non ci sono clan salvati.")
            return

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=clan["name"], callback_data=f"clan_show_{clan['id']}")]
                for clan in saved_clans
            ]
        )

        info_text = "Clan salvati:\n\nSeleziona un clan per visualizzare le informazioni.\n\nPer aggiungere un nuovo clan usa /clan seguito dall'ID"

        await callback.message.answer(text=info_text, reply_markup=kb)
    else:
        await callback.message.answer("Per cercare un nuovo clan usa il comando /clan seguito dall'ID")

@dp.callback_query(lambda c: c.data and c.data.startswith("clan_show_"))
async def handle_show_saved_clan(callback: types.CallbackQuery):
    clan_id = callback.data.split("_", 2)[-1]

    try:
        await callback.message.delete()
    except:
        pass

    url = f"https://api.wolvesville.com/clans/{clan_id}/info"

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                "Accept": "application/json"
            }

            resp = await session.get(url, headers=headers)
            if resp.status != 200:
                await callback.message.answer("Impossibile recuperare le info del clan!")
                return

            clan_info = await resp.json()

        clan_name = clan_info.get("name", "Sconosciuto")

        # Format text with proper HTML tags
        response_text = (
            "<b>Informazioni sul Clan</b>\n\n"
            f"<b>ID:</b> {clan_id}\n"
            f"<b>Nome:</b> {clan_name}\n"
            f"<b>Descrizione:</b> {clan_info.get('description', 'N/A')}\n"
            f"<b>XP:</b> {clan_info.get('xp', 'N/A')}\n"
            f"<b>Lingua:</b> {clan_info.get('language', 'N/A')}\n"
            f"<b>Tag:</b> {clan_info.get('tag', 'N/A')}\n"
            f"<b>Tipo di Unione:</b> {clan_info.get('joinType', 'N/A')}\n"
            f"<b>ID Leader:</b> {clan_info.get('leaderId', 'N/A')}\n"
            f"<b>Conteggio quest:</b> {clan_info.get('questHistoryCount', 'N/A')}\n"
            f"<b>Livello Minimo:</b> {clan_info.get('minLevel', 'N/A')}\n"
            f"<b>Membri:</b> {clan_info.get('memberCount', 'N/A')}\n"
            f"<b>Oro:</b> {clan_info.get('gold', 'N/A')}\n"
            f"<b>Gemme:</b> {clan_info.get('gems', 'N/A')}\n"
        )

        await callback.message.answer(response_text)

    except Exception as e:
        logger.error(f"Errore show clan salvato {clan_id}: {e}")
        await callback.message.answer("Errore durante la ricerca del clan salvato.")

# =============================================================================
# GESTIONE DELLA MODIFICA DELLE DONAZIONI (FLUSSO MULTIPASSO)
# =============================================================================
@dp.callback_query(lambda c: c.data == "close_balances")
async def close_balances_callback(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except Exception as e:
        logger.error(f"Errore nella chiusura del messaggio: {e}")

@dp.callback_query(lambda c: c.data == "modify_start")
async def modify_start(callback: types.CallbackQuery, state: FSMContext):
    """
    Avvia il flusso per la modifica dei bilanci con validazione admin migliorata.
    """
    # Validazione admin con helper
    if not await check_admin_access(callback):
        return

    try:
        players = []
        users = await db_manager.list_users()
        for doc in users:
            username = doc.get("username", "Sconosciuto")
            if username != "Sconosciuto":  # Filtra username validi
                players.append(username)

        if not players:
            await callback.message.answer("❌ Nessun giocatore trovato nel database.")
            return

        await state.update_data(players=players, current_page=0, modify_msg_ids=[])
        kb = create_players_keyboard(players, page=0)
        text_page = make_page_text(0, players, page_size=10)
        msg = await callback.message.answer(text_page, reply_markup=kb)
        await add_modify_msg(state, msg)
        await state.set_state(ModifyStates.CHOOSING_PLAYER)

    except Exception as e:
        logger.error(f"Errore in modify_start: {e}")
        await callback.message.answer("❌ Si è verificato un errore nell'avvio della modifica.")



@dp.callback_query(lambda c: c.data and c.data.startswith("modify_paginate_"))
async def modify_paginate(callback: types.CallbackQuery, state: FSMContext):
    """
    Passo 2: Gestisce la paginazione (pagina successiva/precedente) per la modifica.
    """
    new_page = int(callback.data.split("_")[-1])
    data = await state.get_data()
    players = data.get("players", [])
    if not players:
        await callback.answer("Nessun giocatore in memoria.")
        return
    await state.update_data(current_page=new_page)
    kb = create_players_keyboard(players, page=new_page)
    text_page = make_page_text(new_page, players, page_size=10)
    await callback.message.edit_text(text_page, reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith("modify_player_"))
async def modify_choose_player(callback: types.CallbackQuery, state: FSMContext):
    """
    Passo 3: L'utente sceglie un giocatore; viene richiesto di scegliere la valuta da modificare.
    """
    username = callback.data.split("modify_player_")[-1]
    await state.update_data(chosen_player=username)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Gold", callback_data="modify_currency_Gold"),
         InlineKeyboardButton(text="Gem", callback_data="modify_currency_Gem")],
        [InlineKeyboardButton(text="Indietro", callback_data="modify_start"),
         InlineKeyboardButton(text="Fine", callback_data="modify_finish")]
    ])
    msg = await callback.message.answer(
        f"Hai scelto <b>{username}</b>. Seleziona la valuta da modificare:",
        parse_mode="HTML", reply_markup=kb
    )
    await add_modify_msg(state, msg)
    await state.set_state(ModifyStates.CHOOSING_CURRENCY)


@dp.callback_query(lambda c: c.data and c.data.startswith("modify_currency_"))
async def modify_choose_currency(callback: types.CallbackQuery, state: FSMContext):
    currency = callback.data.split("modify_currency_")[-1]

    # Mappa "Gold" -> "Oro", "Gem" -> "Gem"
    if currency.lower() == "gold":
        db_key = "Oro"
    elif currency.lower() == "gem":
        db_key = "Gem"
    else:
        # Se vuoi gestire altri casi o dare un messaggio di errore
        db_key = currency

    # Salva nel contesto FSM
    await state.update_data(chosen_currency=currency, chosen_db_key=db_key)

    msg = await callback.message.answer(
        f"Inserisci la nuova quantità di <b>{currency}</b>:",
        parse_mode="HTML"
    )
    await add_modify_msg(state, msg)
    await state.set_state(ModifyStates.ENTERING_AMOUNT)


@dp.message(ModifyStates.ENTERING_AMOUNT)
async def modify_enter_amount(message: types.Message, state: FSMContext):
    try:
        amount_text = message.text.strip()

        # Validazione input vuoto
        if not amount_text:
            await message.answer("❌ Inserisci un valore numerico.\n💡 Esempio: 1000")
            return

        # Validazione che sia un numero
        try:
            new_amount = int(amount_text)
        except ValueError:
            await message.answer("❌ Il valore deve essere un numero intero.\n💡 Esempi validi: 500, 1000, -200")
            return

        # Validazione range ragionevole
        if new_amount < -999999 or new_amount > 999999:
            await message.answer("❌ Il valore deve essere tra -999,999 e 999,999.")
            return

        data = await state.get_data()
        username = data.get("chosen_player")
        currency = data.get("chosen_currency")
        db_key = data.get("chosen_db_key")

        if not username or not currency or not db_key:
            await message.answer("❌ Errore nei dati di sessione. Riprova dall'inizio.")
            await state.clear()
            return

        # Aggiorna il DB
        await db_manager.set_user_currency(username, db_key, new_amount)

        msg = await message.answer(
            f"✅ {currency} di <b>{username}</b> aggiornato a: <b>{new_amount:,}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔙 Indietro", callback_data=f"modify_currency_{currency}"),
                    InlineKeyboardButton(text="✅ Fine", callback_data="modify_finish")
                ]
            ])
        )
        await add_modify_msg(state, msg)
        await state.set_state(ModifyStates.CHOOSING_CURRENCY)

    except Exception as e:
        logger.error(f"Errore in modify_enter_amount: {e}")
        await message.answer("❌ Si è verificato un errore. Riprova più tardi.")

    try:
        await message.delete()
    except:
        pass



@dp.callback_query(lambda c: c.data == "modify_finish")
async def modify_finish(callback: types.CallbackQuery, state: FSMContext):
    """
    Passo 6: Conclude il flusso di modifica cancellando tutti i messaggi temporanei.
    """
    data = await state.get_data()
    msg_ids = data.get("modify_msg_ids", [])
    chat_id = callback.message.chat.id
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception as e:
            logger.warning(f"Errore nel cancellare il messaggio {mid}: {e}")
    await state.clear()

# =============================================================================
# GESTIONE DEI MEMBRI E DEL PROFILO GIOCATORE
# =============================================================================
@dp.callback_query(lambda c: c.data and c.data.startswith("is_member_"))
async def handle_member_check(callback: types.CallbackQuery, state: FSMContext):
    """
    Gestisce la verifica se l'utente è un membro del clan:
      - Se sì, recupera e mostra i membri.
      - Se no, chiede di inserire l'username per cercare il profilo.
    """
    choice = callback.data.split("_")[-1]
    try:
        await callback.message.delete()
    except:
        pass

    if choice == "yes":
        try:
            text_loading = "Caricamento in corso..."
            progress_message = await callback.message.answer(text_loading)
        except:
            return

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }
                url = f"https://api.wolvesville.com/clans/{CLAN_ID}/members"
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        await progress_message.delete()
                        text_err = f"Impossibile recuperare i membri del clan. (status={response.status})"
                        await callback.message.answer(text_err)
                        return
                    members = await response.json()

            usernames = [m["username"] for m in members if "username" in m]
            await progress_message.delete()
            if not usernames:
                await callback.message.answer("Nessun membro trovato nel clan.")
                return

            pages = [usernames[i:i + 10] for i in range(0, len(usernames), 10)]
            await state.update_data(pages=pages, current_page=0)
            await show_members_page(callback.message, state)

        except Exception as e:
            logger.error(f"Errore durante il recupero dei membri: {e}")
            await callback.message.answer("Impossibile recuperare i membri del clan!")
    else:
        text_prompt = "Inserisci l'username del profilo che vuoi cercare:"
        prompt_msg = await callback.message.answer(text_prompt)
        await state.update_data(username_prompt_msg_id=prompt_msg.message_id)
        await state.set_state(PlayerStates.PROFILE_SEARCH)

@dp.callback_query(lambda c: c.data and c.data.startswith("navigate_"))
async def handle_navigation(callback: types.CallbackQuery, state: FSMContext):
    """
    Gestisce la navigazione tra le pagine dei membri del clan.
    """
    page_num = int(callback.data.split("_", 1)[1])
    await state.update_data(current_page=page_num)
    try:
        await callback.message.delete()
    except:
        pass
    await show_members_page(callback.message, state)

@dp.callback_query(lambda c: c.data and c.data.startswith("profile_"))
async def handle_profile_callback(callback: types.CallbackQuery, state: FSMContext):
    """
    Avvia la ricerca del profilo utente per il membro selezionato.
    """
    username = callback.data.split("_", 1)[1]
    logger.debug(f"Ricerca profilo (membro) per username: {username}")
    try:
        await callback.message.delete()
    except:
        pass
    await search_by_username(callback.message, username)

def validate_username(username: str) -> tuple[bool, str]:
    """
    Valida un username e ritorna (is_valid, error_message)
    """
    if not username:
        return False, "❌ Username non può essere vuoto."

    if len(username) < 3:
        return False, "❌ Username deve essere almeno 3 caratteri."

    if len(username) > 20:
        return False, "❌ Username non può superare 20 caratteri."

    # Controlla caratteri validi (lettere, numeri, underscore)
    if not all(c.isalnum() or c == '_' for c in username):
        return False, "❌ Username può contenere solo lettere, numeri e underscore (_)."

    return True, ""


@dp.message(PlayerStates.PROFILE_SEARCH)
async def search_profile(message: types.Message, state: FSMContext):
    """
    Cerca il profilo in base all'username inserito con validazione.
    """
    try:
        data = await state.get_data()
        prompt_msg_id = data.get("username_prompt_msg_id")
        if prompt_msg_id:
            try:
                await message.bot.delete_message(message.chat.id, prompt_msg_id)
            except:
                pass

        username = message.text.strip()

        # VALIDAZIONE USERNAME
        is_valid, error_msg = validate_username(username)
        if not is_valid:
            # Invia messaggio di errore e mantieni lo stato per riprovare
            error_message = await message.answer(
                f"{error_msg}\n\n💡 Riprova inserendo un username valido:"
            )
            await state.update_data(username_prompt_msg_id=error_message.message_id)
            return

        logger.debug(f"Ricerca profilo per username: {username}")
        await search_by_username(message, username)

    except Exception as e:
        logger.error(f"Errore in search_profile: {e}")
        await message.answer("❌ Si è verificato un errore. Riprova più tardi.")
    finally:
        try:
            await message.delete()
        except:
            pass
        await state.clear()



async def search_by_username(sender_message: types.Message, username: str):
    """
    Cerca un profilo e risponde con i dati formattati ed eventualmente l'avatar equipaggiato.
    """
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            url = f"https://api.wolvesville.com/players/search?username={username}"
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    await send_not_exists(sender_message, username)
                    return
                player_data = await response.json()
                logger.debug(f"Dati ricevuti per {username}: {player_data}")

                if not player_data:
                    await send_not_exists(sender_message, username)
                    return

                if isinstance(player_data, list):
                    player_info = player_data[0] if player_data else None
                else:
                    player_info = player_data

                if not player_info or "id" not in player_info:
                    await send_not_exists(sender_message, username)
                    return

                info_text = format_player_info(player_info)
                eq = player_info.get('equippedAvatar', {})
                eq_url = eq.get('url', '')
                if eq_url:
                    eq_url_hd = await get_best_resolution_url(eq_url)
                else:
                    eq_url_hd = ""

                avatars = player_info.get('avatars', [])
                has_avatars = len(avatars) > 0

                if eq_url_hd:
                    kb = None
                    if has_avatars:
                        kb = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(
                                text="👀 Sì, mostra avatar",
                                callback_data=f"avatars_yes_{player_info['id']}"
                            ),
                            InlineKeyboardButton(
                                text="❌ No",
                                callback_data=f"avatars_no_{player_info['id']}"
                            )
                        ]])
                    await sender_message.answer_photo(
                        photo=eq_url_hd,
                        caption=info_text,
                        reply_markup=kb
                    )
                else:
                    if has_avatars:
                        kb = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(
                                text="👀 Sì, mostra avatar",
                                callback_data=f"avatars_yes_{player_info['id']}"
                            ),
                            InlineKeyboardButton(
                                text="❌ No",
                                callback_data=f"avatars_no_{player_info['id']}"
                            )
                        ]])
                        await sender_message.answer(info_text, reply_markup=kb)
                    else:
                        await sender_message.answer(info_text)

    except Exception as e:
        logger.error(f"Errore generico durante la ricerca di {username}: {e}")
        await send_not_exists(sender_message, username)

@dp.callback_query(lambda c: c.data and c.data.startswith("avatars_"))
async def show_avatars_callback(callback: types.CallbackQuery):
    """
    Gestisce la visualizzazione degli avatar disponibili per il giocatore.
    """
    _, decision, player_id = callback.data.split("_", 2)
    try:
        await callback.message.edit_reply_markup(None)
    except Exception as e:
        logger.warning(f"Impossibile rimuovere la tastiera: {e}")

    if decision == "yes":
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {WOLVESVILLE_API_KEY}",
                    "Accept": "application/json"
                }
                url = f"https://api.wolvesville.com/players/{player_id}"
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        return
                    player_info = await response.json()

            avatars = player_info.get('avatars', [])
            if not avatars:
                return

            for i, av in enumerate(avatars):
                letter = chr(65 + i)
                if letter > 'X':
                    break
                av_url = av.get('url', '')
                if not av_url:
                    continue
                best_url = await get_best_resolution_url(av_url)
                try:
                    async with aiohttp.ClientSession() as session2:
                        async with session2.get(best_url) as r_img:
                            if r_img.status == 200:
                                raw = await r_img.read()
                                await callback.message.answer_photo(
                                    photo=types.BufferedInputFile(raw, filename=f"avatar_{letter}.png"),
                                    caption=f"Slot {letter}"
                                )
                except Exception as e:
                    logger.warning(f"Errore avatar {best_url}: {e}")

        except Exception as e:
            logger.error(f"Errore durante l'invio avatar: {e}")

async def send_not_exists(sender_message: types.Message, username: str):
    """
    Invia un messaggio che informa che l'utente cercato non esiste.
    """
    final_text = f"L'utente {username} non esiste!"
    await sender_message.answer(final_text)

async def show_members_page(message: types.Message, state: FSMContext):
    """
    Mostra la pagina corrente dei membri del clan, con la relativa tastiera di navigazione.
    """
    data = await state.get_data()
    pages = data.get("pages", [])
    current_page = data.get("current_page", 0)
    if not pages:
        await message.answer("Nessun membro trovato nel clan.")
        return
    membri_correnti = pages[current_page]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=u, callback_data=f"profile_{u}")]
            for u in membri_correnti
        ]
    )
    navigation_buttons = []
    if current_page > 0:
        navigation_buttons.append(
            InlineKeyboardButton(text="⬅️ Indietro", callback_data=f"navigate_{current_page - 1}")
        )
    if current_page < len(pages) - 1:
        navigation_buttons.append(
            InlineKeyboardButton(text="➡️ Avanti", callback_data=f"navigate_{current_page + 1}")
        )
    if navigation_buttons:
        keyboard.inline_keyboard.append(navigation_buttons)
    text_page = f"Pagina {current_page + 1}/{len(pages)}:"
    await message.answer(text_page, reply_markup=keyboard)

# =============================================================================
# FUNZIONE MAIN
# =============================================================================
async def main():
    maybe_log_public_ip()
    setup_scheduler()
    await prepopulate_users()

    # NUOVO: Notifica avvio bot
    try:
        await notification_service.send_bot_status_update(
            "AVVIATO",
            f"Bot inizializzato correttamente con sistemi di sicurezza attivi. Gruppi autorizzati: {len(AUTHORIZED_GROUPS)}"
        )
    except Exception as e:
        bot_logger.log_error(e, "Errore invio notifica avvio bot")

    logger.info("Avvio del bot.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
