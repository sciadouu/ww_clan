"""Utility di alto livello per generare e pubblicare statistiche del clan."""

from __future__ import annotations

import io
import logging
from typing import Dict, Optional, Sequence

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from aiogram import types

from services.notification_service import (
    EnhancedNotificationService,
    NotificationType,
)


class StatisticsService:
    """Raccoglie funzioni statistiche e di reporting per il clan."""

    def __init__(
        self,
        db_manager,
        *,
        notification_service: Optional[EnhancedNotificationService] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.db = db_manager
        self.notification_service = notification_service
        self.logger = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    async def _load_donation_dataframe(self, days: int) -> pd.DataFrame:
        donations_data = await self.db.get_donation_time_series(days=days)
        df = pd.DataFrame(donations_data)

        if df.empty:
            df = pd.DataFrame(columns=["date", "gold", "gems", "donations"])

        for column in ("date", "gold", "gems", "donations"):
            if column not in df:
                df[column] = 0

        try:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
        except Exception:  # pragma: no cover - conversione difensiva
            pass

        df = df.sort_values("date")
        return df

    @staticmethod
    def _format_amount(value: float | int) -> str:
        return f"{int(round(value)):,}".replace(",", ".")

    # ------------------------------------------------------------------
    # Donazioni
    # ------------------------------------------------------------------
    async def generate_donation_trends(self, days: int = 30) -> io.BytesIO:
        """Genera un grafico temporale delle donazioni aggregate per valuta."""

        df = await self._load_donation_dataframe(days)

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

    async def publish_donation_report(self, *, days: int, title: str) -> None:
        """Invia un report riassuntivo delle donazioni nel periodo indicato."""

        if not self.notification_service:
            self.logger.debug(
                "Servizio notifiche non configurato: report donazioni %s saltato.",
                title,
            )
            return

        df = await self._load_donation_dataframe(days)
        total_gold = int(df.get("gold", pd.Series(dtype=float)).sum()) if not df.empty else 0
        total_gems = int(df.get("gems", pd.Series(dtype=float)).sum()) if not df.empty else 0
        total_donations = int(df.get("donations", pd.Series(dtype=float)).sum()) if not df.empty else 0

        top_donors: Sequence[Dict] = await self.db.get_top_donors(days=days, limit=5)

        lines = [
            f"📊 *Report {title} donazioni*",
            "",
            f"• Totale Oro: {self._format_amount(total_gold)}",
            f"• Totale Gem: {self._format_amount(total_gems)}",
            f"• Donazioni registrate: {self._format_amount(total_donations)}",
        ]

        if top_donors:
            lines.append("")
            lines.append("🥇 Top donatori:")
            for index, donor in enumerate(top_donors, start=1):
                username = donor.get("username", "Sconosciuto")
                donor_total = donor.get("total_amount", 0) or 0
                gold = donor.get("total_gold", 0) or 0
                gems = donor.get("total_gems", 0) or 0
                lines.append(
                    (
                        f"{index}. *{username}* — {self._format_amount(donor_total)} tot. "
                        f"({self._format_amount(gold)} oro / {self._format_amount(gems)} gem)"
                    )
                )

        caption = "\n".join(lines).strip()

        channel_id = getattr(self.notification_service, "admin_channel_id", None)

        try:
            buffer = await self.generate_donation_trends(days=days)
            if channel_id:
                buffer.seek(0)
                photo = types.BufferedInputFile(
                    buffer.read(), filename="donation_report.png"
                )
                await self.notification_service.bot.send_photo(
                    chat_id=channel_id,
                    photo=photo,
                    caption=caption,
                    parse_mode="Markdown",
                )
                return
        except Exception as exc:  # pragma: no cover - log difensivo
            self.logger.warning("Invio grafico donazioni fallito: %s", exc)

        try:
            await self.notification_service.send_admin_notification(
                caption,
                NotificationType.INFO,
                disable_rate_limit=True,
            )
        except Exception as exc:  # pragma: no cover - log difensivo
            self.logger.error("Invio report donazioni fallito: %s", exc)

    async def publish_weekly_donation_report(self) -> None:
        await self.publish_donation_report(days=7, title="settimanale")

    async def publish_monthly_donation_report(self) -> None:
        await self.publish_donation_report(days=30, title="mensile")

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
