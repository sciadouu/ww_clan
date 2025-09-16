from datetime import datetime, timedelta
from typing import Dict, List
import asyncio

class RewardService:
    def __init__(self, db_manager):
        self.db = db_manager
        
        # Configurazione punti
        self.POINTS_CONFIG = {
            "DONATION_ORO": 1,      # 1 punto per 1000 oro
            "DONATION_GEM": 2,      # 2 punti per 1000 gem  
            "MISSION_PARTICIPATION": 5,
            "WEEKLY_BONUS": 10,
            "MONTHLY_BONUS": 50
        }
        
        # Achievement definitions
        self.ACHIEVEMENTS = {
            "FIRST_DONATION": {"points": 10, "name": "Prima Donazione", "icon": "🎯"},
            "BIG_DONOR": {"points": 25, "name": "Grande Donatore", "icon": "💎"},
            "MISSION_VETERAN": {"points": 30, "name": "Veterano Missioni", "icon": "⚔️"},
            "CLAN_LEGEND": {"points": 100, "name": "Leggenda del Clan", "icon": "👑"}
        }
        
    async def award_points(self, username: str, point_type: str, amount: int = None):
        """Assegna punti a un utente"""
        if point_type == "DONATION_ORO":
            points = max(1, amount // 1000) * self.POINTS_CONFIG["DONATION_ORO"]
        elif point_type == "DONATION_GEM":
            points = max(1, amount // 1000) * self.POINTS_CONFIG["DONATION_GEM"]
        else:
            points = self.POINTS_CONFIG.get(point_type, 0)
            
        # Aggiorna database
        await self.db.users_col.update_one(
            {"username": username},
            {"$inc": {"reward_points": points}},
            upsert=True
        )
        
        # Controlla achievement
        await self.check_achievements(username)
        
        return points
        
    async def check_achievements(self, username: str):
        """Controlla se l'utente ha sbloccato achievement"""
        user_data = await self.db.users_col.find_one({"username": username})
        if not user_data:
            return
            
        donazioni = user_data.get("donazioni", {})
        total_oro = donazioni.get("Oro", 0)
        total_gem = donazioni.get("Gem", 0)
        current_achievements = user_data.get("achievements", [])
        
        new_achievements = []
        
        # Prima donazione
        if "FIRST_DONATION" not in current_achievements and (total_oro > 0 or total_gem > 0):
            new_achievements.append("FIRST_DONATION")
            
        # Grande donatore (50k oro o 20k gem)
        if "BIG_DONOR" not in current_achievements and (total_oro >= 50000 or total_gem >= 20000):
            new_achievements.append("BIG_DONOR")
            
        # Aggiorna database con nuovi achievement
        if new_achievements:
            await self.db.users_col.update_one(
                {"username": username},
                {"$addToSet": {"achievements": {"$each": new_achievements}}}
            )
            
        return new_achievements
        
    async def get_leaderboard(self, limit: int = 10) -> List[Dict]:
        """Genera leaderboard punti"""
        pipeline = [
            {"$match": {"reward_points": {"$gt": 0}}},
            {"$sort": {"reward_points": -1}},
            {"$limit": limit}
        ]
        
        leaderboard = await self.db.users_col.aggregate(pipeline).to_list(length=None)
        return leaderboard
