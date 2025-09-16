import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List
import io

class StatisticsService:
    def __init__(self, db_manager):
        self.db = db_manager
        
    async def generate_donation_trends(self, days: int = 30) -> io.BytesIO:
        """Genera grafico trend donazioni ultimi N giorni"""
        # Recupera dati donazioni
        donations_data = await self.db.get_donation_history(days)
        
        df = pd.DataFrame(donations_data)
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))
        
        # Grafico donazioni Oro nel tempo
        df_oro = df.groupby('date')['oro'].sum()
        ax1.plot(df_oro.index, df_oro.values, marker='o', color='gold')
        ax1.set_title('Trend Donazioni Oro')
        ax1.set_ylabel('Oro Donato')
        
        # Grafico donazioni Gem nel tempo  
        df_gem = df.groupby('date')['gem'].sum()
        ax2.plot(df_gem.index, df_gem.values, marker='o', color='purple')
        ax2.set_title('Trend Donazioni Gem')
        ax2.set_ylabel('Gem Donate')
        
        plt.tight_layout()
        
        # Salva in buffer
        buffer = io.BytesIO()
        plt.savefig(buffer, format='png', dpi=300, bbox_inches='tight')
        buffer.seek(0)
        plt.close()
        
        return buffer
        
    async def generate_participation_stats(self) -> Dict:
        """Genera statistiche partecipazione missioni"""
        missions_data = await self.db.get_mission_participation()
        
        stats = {
            "total_missions": len(missions_data),
            "avg_participants": sum(m["participants"] for m in missions_data) / len(missions_data),
            "most_active_players": await self.db.get_top_participants(limit=10),
            "mission_success_rate": await self.db.calculate_success_rate()
        }
        
        return stats
