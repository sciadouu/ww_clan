from datetime import datetime, timedelta
import asyncio
from typing import List, Dict

class CalendarService:
    def __init__(self, bot, notification_service):
        self.bot = bot
        self.notification_service = notification_service
        self.events = []  # Lista eventi programmati
        
    async def add_reminder(self, event_name: str, datetime_trigger: datetime, 
                          chat_ids: List[int], message: str):
        """Aggiunge reminder personalizzato"""
        event = {
            "id": len(self.events) + 1,
            "name": event_name,
            "trigger_time": datetime_trigger,
            "chat_ids": chat_ids,
            "message": message,
            "sent": False
        }
        self.events.append(event)
        
    async def check_pending_reminders(self):
        """Controlla e invia reminder scaduti"""
        now = datetime.now()
        
        for event in self.events:
            if not event["sent"] and now >= event["trigger_time"]:
                # Invia reminder
                for chat_id in event["chat_ids"]:
                    try:
                        await self.bot.send_message(
                            chat_id=chat_id,
                            text=f"🔔 **Reminder: {event['name']}**\n\n{event['message']}"
                        )
                    except Exception as e:
                        logging.error(f"Errore invio reminder a {chat_id}: {e}")
                
                event["sent"] = True
                
    async def schedule_clan_events(self):
        """Programma eventi clan ricorrenti"""
        # Reminder votazione missioni (ogni lunedì 8:00)
        next_monday = datetime.now() + timedelta(days=(0-datetime.now().weekday()) % 7)
        next_monday = next_monday.replace(hour=8, minute=0, second=0)
        
        await self.add_reminder(
            event_name="Votazione Missioni Settimanali",
            datetime_trigger=next_monday,
            chat_ids=[CHAT_ID],
            message="🗳️ È tempo di votare le missioni della settimana!\nUsate /menu > Missione per vedere le opzioni disponibili."
        )
