import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
from typing import Dict
import io

class StatisticsService:
    def __init__(self, db_manager):
        self.db = db_manager
        
    async def generate_donation_trends(self, days: int = 30) -> io.BytesIO:
        """Genera un grafico temporale delle donazioni aggregate per valuta."""

        donations_data = await self.db.get_donation_time_series(days=days)
        df = pd.DataFrame(donations_data)

        if df.empty:
            df = pd.DataFrame(columns=["date", "gold", "gems", "donations"])

        for column in ("gold", "gems", "donations"):
            if column not in df:
                df[column] = 0

        if "date" in df:
            try:
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
            except Exception:  # pragma: no cover - conversione difensiva
                pass
            df = df.sort_values("date")

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))

        df_oro = df.groupby("date")["gold"].sum() if not df.empty else pd.Series(dtype=float)
        ax1.plot(df_oro.index, df_oro.values, marker="o", color="gold")
        ax1.set_title("Trend Donazioni Oro")
        ax1.set_ylabel("Oro Donato")

        df_gem = df.groupby("date")["gems"].sum() if not df.empty else pd.Series(dtype=float)
        ax2.plot(df_gem.index, df_gem.values, marker="o", color="purple")
        ax2.set_title("Trend Donazioni Gem")
        ax2.set_ylabel("Gem Donate")

        plt.tight_layout()

        buffer = io.BytesIO()
        plt.savefig(buffer, format="png", dpi=300, bbox_inches="tight")
        buffer.seek(0)
        plt.close()

        return buffer

    async def generate_participation_stats(self) -> Dict:
        """Genera statistiche partecipazione missioni"""
        missions_data = await self.db.get_mission_participation()

        total_missions = len(missions_data)
        avg_participants = (
            sum(m["participants"] for m in missions_data) / total_missions
            if total_missions
            else 0
        )

        stats = {
            "total_missions": total_missions,
            "avg_participants": avg_participants,
            "most_active_players": await self.db.get_top_participants(limit=10),
            "mission_success_rate": await self.db.calculate_success_rate(),
            "time_series": await self.db.get_mission_time_series(),
        }

        return stats
