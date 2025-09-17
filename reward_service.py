from typing import Dict, List, Optional

from services.db_manager import MongoManager

class RewardService:
    def __init__(self, db_manager: MongoManager):
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
        
    async def award_points(
        self, username: str, point_type: str, amount: Optional[int] = None
    ) -> int:
        """Assegna punti a un utente"""
        safe_amount = max(amount or 0, 0)

        if point_type == "DONATION_ORO":
            base_units = safe_amount // 1000
            if safe_amount > 0:
                base_units = max(1, base_units)
            points = base_units * self.POINTS_CONFIG["DONATION_ORO"]
        elif point_type == "DONATION_GEM":
            base_units = safe_amount // 1000
            if safe_amount > 0:
                base_units = max(1, base_units)
            points = base_units * self.POINTS_CONFIG["DONATION_GEM"]
        else:
            points = self.POINTS_CONFIG.get(point_type, 0)

        if points <= 0:
            return 0

        # Aggiorna database
        await self.db.increment_reward_points(username, points)

        # Controlla achievement
        await self.check_achievements(username)

        return points
        
    async def check_achievements(self, username: str):
        """Controlla se l'utente ha sbloccato achievement"""
        user_data = await self.db.fetch_user(username)
        if not user_data:
            return []
            
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
            await self.db.add_achievements(username, new_achievements)

        return new_achievements
        
    async def get_leaderboard(self, limit: int = 10) -> List[Dict]:
        """Genera leaderboard punti"""
        pipeline = [
            {"$match": {"reward_points": {"$gt": 0}}},
            {"$sort": {"reward_points": -1}},
            {"$limit": limit}
        ]
        
        leaderboard = await self.db.aggregate_users(pipeline)
        return leaderboard
