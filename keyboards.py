# keyboards.py
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def main_menu():
    """Menu principale con pulsanti inline."""
    buttons = [
        [InlineKeyboardButton("👤 Giocatore", callback_data="player_menu")],
        [InlineKeyboardButton("🏰 Clan", callback_data="clan_menu")],
        [InlineKeyboardButton("📩 Messaggio", callback_data="message_menu")],
        [InlineKeyboardButton("📢 Annuncio", callback_data="announcement_menu")],
        [InlineKeyboardButton("⏩ Skip", callback_data="skip_menu")],
        [InlineKeyboardButton("❓ Help", callback_data="help_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
