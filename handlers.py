from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()

@router.message(Command("start"))
async def start_handler(message: Message):
    await message.answer("Ciao! Sono il tuo bot. Usa il menu per iniziare.")

@router.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(
        "Ecco i comandi disponibili:\n"
        "/messaggio - Invia un messaggio al clan\n"
        "/annuncio - Pubblica un annuncio\n"
        "/player - Cerca un giocatore\n"
        "/clan - Visualizza informazioni del clan\n"
        "/skip - Salta il tempo di attesa della quest"
    )


def setup_handlers(dp):
    dp.include_router(router)
