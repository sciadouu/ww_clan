"""Servizi di alto livello per la gestione delle ricompense del clan."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from services.notification_service import EnhancedNotificationService, NotificationType
from services.rewards_repository import RewardsRepository


def _ensure_timezone(value: datetime) -> datetime:
    """Garantisce che il datetime sia timezone-aware in UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(slots=True)
class RewardService:
    """Coordinatore dell'intero ecosistema reward."""

    repository: RewardsRepository
    notification_service: Optional[EnhancedNotificationService] = None
    logger: Any = None
    _local_tz: ZoneInfo = field(init=False, repr=False)
    POINTS_CONFIG: Dict[str, Dict[str, Any]] = field(init=False)
    POINT_TYPE_ALIASES: Dict[str, str] = field(init=False)
    _period_aliases: Dict[str, Dict[str, Any]] = field(init=False, repr=False)
    ACHIEVEMENTS: Dict[str, Dict[str, Any]] = field(init=False)

    def __post_init__(self) -> None:
        self.logger = self.logger or logging.getLogger(__name__)
        self._local_tz = ZoneInfo("Europe/Rome")

        self.POINTS_CONFIG: Dict[str, Dict[str, Any]] = {
            "DONATION_ORO": {
                "mode": "donation",
                "per_amount": 1000,
                "multiplier": 1,
                "min_points": 1,
                "label": "Donazioni Oro",
            },
            "DONATION_GEM": {
                "mode": "donation",
                "per_amount": 1000,
                "multiplier": 2,
                "min_points": 2,
                "label": "Donazioni Gem",
            },
            "MISSION_PARTICIPATION": {
                "mode": "fixed",
                "points": 5,
                "label": "Partecipazione Missione",
            },
            "MISSION_SUCCESS": {
                "mode": "fixed",
                "points": 8,
                "label": "Missione Completata",
            },
            "MISSION_SUPPORT": {
                "mode": "ratio",
                "ratio": 0.5,
                "label": "Supporto Missione",
            },
            "WEEKLY_BONUS": {
                "mode": "fixed",
                "points": 10,
                "label": "Bonus Settimanale",
            },
            "MONTHLY_BONUS": {
                "mode": "fixed",
                "points": 50,
                "label": "Bonus Mensile",
            },
            "EVENT_BONUS": {
                "mode": "fixed",
                "points": 20,
                "label": "Evento Speciale",
            },
            "TRAINING_ATTENDANCE": {
                "mode": "fixed",
                "points": 3,
                "label": "Allenamento",
            },
            "RAID_VICTORY": {
                "mode": "fixed",
                "points": 25,
                "label": "Vittoria Raid",
            },
            "DAILY_LOGIN": {
                "mode": "fixed",
                "points": 1,
                "label": "Accesso Giornaliero",
            },
            "ACHIEVEMENT_BONUS": {
                "mode": "fixed",
                "points": 0,
                "label": "Bonus Achievement",
            },
            "PENALTY": {
                "mode": "fixed",
                "points": -5,
                "allow_negative": True,
                "label": "Penalità",
            },
        }

        self.POINT_TYPE_ALIASES: Dict[str, str] = {
            "oro": "DONATION_ORO",
            "gold": "DONATION_ORO",
            "donation_oro": "DONATION_ORO",
            "gem": "DONATION_GEM",
            "gems": "DONATION_GEM",
            "gemme": "DONATION_GEM",
            "donation_gem": "DONATION_GEM",
            "support": "MISSION_SUPPORT",
            "mission_support": "MISSION_SUPPORT",
            "training": "TRAINING_ATTENDANCE",
            "allenamento": "TRAINING_ATTENDANCE",
            "raid": "RAID_VICTORY",
            "raid_victory": "RAID_VICTORY",
            "daily": "DAILY_LOGIN",
            "login": "DAILY_LOGIN",
        }

        self._period_aliases: Dict[str, Dict[str, Any]] = {
            "all": {"aliases": {"", "all", "totale", "sempre", "overall"}, "label": "generale"},
            "weekly": {
                "aliases": {"week", "weekly", "settimana", "settimanale"},
                "label": "settimanale",
            },
            "monthly": {
                "aliases": {"month", "monthly", "mese", "mensile"},
                "label": "mensile",
            },
            "daily": {
                "aliases": {"day", "daily", "oggi", "giorno", "giornaliera"},
                "label": "giornaliera",
            },
        }

        self.ACHIEVEMENTS: Dict[str, Dict[str, Any]] = {
            "FIRST_DONATION": {
                "points_bonus": 10,
                "name": "Prima Donazione",
                "icon": "🎯",
                "description": "Effettua la tua prima donazione in oro o gemme.",
                "criteria": {
                    "any": [
                        {"metric": "donations.Oro", "gte": 1},
                        {"metric": "donations.Gem", "gte": 1},
                        {"metric": "history.by_type.DONATION_ORO.events", "gte": 1},
                        {"metric": "history.by_type.DONATION_GEM.events", "gte": 1},
                    ]
                },
            },
            "BIG_DONOR": {
                "points_bonus": 25,
                "name": "Grande Donatore",
                "icon": "💎",
                "description": "Raggiungi 50k oro o 20k gemme donate complessivamente.",
                "criteria": {
                    "any": [
                        {"metric": "donations.Oro", "gte": 50000},
                        {"metric": "donations.Gem", "gte": 20000},
                    ]
                },
            },
            "MISSION_VETERAN": {
                "points_bonus": 30,
                "name": "Veterano Missioni",
                "icon": "⚔️",
                "description": "Partecipa ad almeno 10 missioni monitorate.",
                "criteria": {
                    "metric": "history.by_type.MISSION_PARTICIPATION.events",
                    "gte": 10,
                },
            },
            "CLAN_LEGEND": {
                "points_bonus": 50,
                "name": "Leggenda del Clan",
                "icon": "👑",
                "description": "Supera i 1000 punti ricompensa totali.",
                "criteria": {"metric": "reward_points", "gte": 1000},
            },
            "SUPPORT_SPECIALIST": {
                "points_bonus": 15,
                "name": "Specialista di Supporto",
                "icon": "🛡️",
                "description": "Accumula punti assistendo le missioni degli alleati.",
                "criteria": {
                    "any": [
                        {
                            "metric": "history.by_type.MISSION_SUPPORT.events",
                            "gte": 5,
                        },
                        {
                            "metric": "history.by_type.MISSION_SUPPORT.points",
                            "gte": 25,
                        },
                    ]
                },
            },
            "DAILY_GRINDER": {
                "points_bonus": 10,
                "name": "Costanza Quotidiana",
                "icon": "🗓️",
                "description": "Raccogli premi partecipando ogni giorno alle attività del clan.",
                "criteria": {
                    "metric": "history.by_type.DAILY_LOGIN.events",
                    "gte": 7,
                },
            },
            "RESOURCE_TYCOON": {
                "points_bonus": 40,
                "name": "Magnate delle Risorse",
                "icon": "🏦",
                "description": "Supera gli obiettivi di donazioni oro e gemme nel lungo periodo.",
                "criteria": {
                    "all": [
                        {
                            "metric": "history.by_type.DONATION_ORO.total_amount",
                            "gte": 100000,
                        },
                        {
                            "metric": "history.by_type.DONATION_GEM.total_amount",
                            "gte": 5000,
                        },
                    ]
                },
            },
        }

    # ------------------------------------------------------------------
    # Normalizzazioni e helper
    # ------------------------------------------------------------------
    def normalize_period(self, period: Optional[str]) -> str:
        normalized = (period or "").strip().lower()
        for canonical, info in self._period_aliases.items():
            if normalized in info["aliases"]:
                return canonical
        return "all"

    def _period_label(self, period: str) -> str:
        info = self._period_aliases.get(period, self._period_aliases["all"])
        return info.get("label", period)

    def _normalize_point_type(self, point_type: str) -> Optional[str]:
        if not point_type:
            return None
        normalized = point_type.strip().upper()
        if normalized in self.POINTS_CONFIG:
            return normalized
        alias = self.POINT_TYPE_ALIASES.get(normalized.lower())
        return alias

    def _normalize_amount(self, amount: Any, *, allow_negative: bool = False) -> int:
        if amount is None:
            return 0

        numeric: float
        if isinstance(amount, (int, float)):
            numeric = float(amount)
        else:
            try:
                raw_text = str(amount).strip().lower()
            except Exception:
                return 0

            if not raw_text:
                return 0

            multiplier = 1.0
            if raw_text.endswith(("k", "m")):
                suffix = raw_text[-1]
                raw_text = raw_text[:-1].strip()
                if suffix == "k":
                    multiplier = 1000.0
                elif suffix == "m":
                    multiplier = 1_000_000.0

            # Rimuove separatori migliaia comuni e normalizza la parte decimale.
            sanitized = raw_text.replace("'", "").replace("_", "").replace(" ", "")
            if "," in sanitized and "." in sanitized:
                if sanitized.rfind(",") > sanitized.rfind("."):
                    sanitized = sanitized.replace(".", "").replace(",", ".")
                else:
                    sanitized = sanitized.replace(",", "")
            elif sanitized.count(".") > 1 and "," not in sanitized:
                sanitized = sanitized.replace(".", "")
            elif sanitized.count(",") > 1 and "." not in sanitized:
                sanitized = sanitized.replace(",", "")
            else:
                sanitized = sanitized.replace(",", ".")

            try:
                numeric = float(sanitized) * multiplier
            except (TypeError, ValueError):
                return 0

        if not allow_negative:
            numeric = max(numeric, 0.0)

        return int(round(numeric))

    def _compute_points(self, config: Dict[str, Any], amount: int) -> int:
        mode = config.get("mode", "fixed")

        if mode == "donation":
            if amount <= 0:
                return 0
            per_amount = max(int(config.get("per_amount", 1000)), 1)
            multiplier = int(config.get("multiplier", 1))
            min_points = int(config.get("min_points", 0))
            units = amount // per_amount
            computed = units * multiplier
            if computed <= 0 and min_points > 0:
                computed = min_points
            elif min_points > 0:
                computed = max(computed, min_points)
            return int(computed)

        if mode == "ratio":
            ratio = float(config.get("ratio", 1.0))
            value = amount * ratio
            return int(round(value))

        points = int(config.get("points", 0))
        if config.get("allow_override") and amount:
            points = int(amount)
        return points

    def _format_points(self, value: int) -> str:
        return f"{int(value):,}".replace(",", ".")

    def _point_type_label(self, point_type: str) -> str:
        config = self.POINTS_CONFIG.get(point_type, {})
        return config.get("label", point_type.replace("_", " ").title())

    def _achievement_icons(self, achievements: Sequence[str]) -> str:
        icons = [self.ACHIEVEMENTS.get(code, {}).get("icon", "") for code in achievements]
        return "".join(icon for icon in icons if icon)

    def _format_timestamp(self, value: Any) -> str:
        if not isinstance(value, datetime):
            return ""
        aware = _ensure_timezone(value)
        local = aware.astimezone(self._local_tz)
        return local.strftime("%d/%m %H:%M")

    async def _build_metrics(self, username: str, user_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        donations_raw = user_snapshot.get("donazioni", {}) or {}
        donations = {
            key: int(value or 0)
            for key, value in donations_raw.items()
            if isinstance(key, str)
        }

        history_stats = await self.repository.get_user_point_breakdown(username)

        return {
            "reward_points": int(user_snapshot.get("reward_points", 0) or 0),
            "donations": donations,
            "history": history_stats,
            "achievements": set(user_snapshot.get("achievements", []) or []),
        }

    def _extract_metric(self, metrics: Dict[str, Any], path: str) -> Any:
        if not path:
            return None
        parts = path.split(".")
        current: Any = metrics
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def _evaluate_criteria(self, criteria: Dict[str, Any], metrics: Dict[str, Any]) -> bool:
        if not criteria:
            return False

        if "all" in criteria:
            return all(self._evaluate_criteria(item, metrics) for item in criteria["all"])
        if "any" in criteria:
            return any(self._evaluate_criteria(item, metrics) for item in criteria["any"])

        metric_path = criteria.get("metric")
        value = self._extract_metric(metrics, metric_path)

        if value is None:
            return False

        if "in" in criteria:
            expected = set(criteria["in"])
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return bool(expected.intersection(value))
            return value in expected

        comparisons = {
            "gte": lambda a, b: a >= b,
            "gt": lambda a, b: a > b,
            "lte": lambda a, b: a <= b,
            "lt": lambda a, b: a < b,
            "eq": lambda a, b: a == b,
        }

        numeric_value: Optional[float]
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            numeric_value = None

        for operator, comparator in comparisons.items():
            if operator in criteria and numeric_value is not None:
                return comparator(numeric_value, float(criteria[operator]))

        return bool(value)

    # ------------------------------------------------------------------
    # API pubbliche
    # ------------------------------------------------------------------
    async def award_points(
        self,
        username: str,
        point_type: str,
        *,
        amount: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        canonical_point_type = self._normalize_point_type(point_type)
        if not canonical_point_type:
            self.logger.warning("Tipologia punti sconosciuta: %s", point_type)
            return {"awarded_points": 0, "total_points": None}

        canonical_username = await self.repository.resolve_username(username)
        if not canonical_username:
            self.logger.warning("Impossibile normalizzare username per award_points: %s", username)
            return {"awarded_points": 0, "total_points": None}

        config = self.POINTS_CONFIG[canonical_point_type]
        normalized_amount = self._normalize_amount(
            amount,
            allow_negative=config.get("allow_negative", False),
        )
        points = self._compute_points(config, normalized_amount)

        if points == 0:
            return {
                "username": canonical_username,
                "awarded_points": 0,
                "total_points": None,
            }

        increment_result = await self.repository.increment_points(
            canonical_username,
            points,
            point_type=canonical_point_type,
            amount=normalized_amount,
            metadata=metadata or {},
        )

        user_snapshot = await self.repository.get_user(canonical_username) or {}
        new_achievements = await self.check_achievements(canonical_username, user_snapshot=user_snapshot)

        return {
            "username": canonical_username,
            "awarded_points": points,
            "total_points": increment_result.get("new_total"),
            "point_type": canonical_point_type,
            "normalized_amount": normalized_amount,
            "new_achievements": new_achievements,
        }

    async def check_achievements(
        self,
        username: str,
        *,
        user_snapshot: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not username:
            return []

        snapshot = user_snapshot or await self.repository.get_user(username)
        if not snapshot:
            return []

        metrics = await self._build_metrics(username, snapshot)
        unlocked: List[Dict[str, Any]] = []
        current = metrics.get("achievements", set())

        for code, definition in self.ACHIEVEMENTS.items():
            if code in current:
                continue
            if self._evaluate_criteria(definition.get("criteria", {}), metrics):
                appended = await self.repository.append_achievement(
                    username,
                    code,
                    {
                        "name": definition.get("name"),
                        "icon": definition.get("icon"),
                        "description": definition.get("description"),
                    },
                )
                if not appended:
                    continue

                current.add(code)
                unlocked.append(
                    {
                        "code": code,
                        "name": definition.get("name"),
                        "icon": definition.get("icon"),
                        "points_bonus": definition.get("points_bonus", 0),
                    }
                )

                bonus = int(definition.get("points_bonus", 0) or 0)
                if bonus:
                    await self.repository.increment_points(
                        username,
                        bonus,
                        point_type="ACHIEVEMENT_BONUS",
                        amount=0,
                        metadata={"achievement": code},
                    )

        if unlocked and self.notification_service:
            try:
                message_lines = [
                    f"🏅 Nuovi achievement per *{username}*:",
                    "",
                ]
                for achievement in unlocked:
                    icon = achievement.get("icon", "🏅")
                    name = achievement.get("name", achievement.get("code"))
                    bonus = achievement.get("points_bonus", 0)
                    if bonus:
                        message_lines.append(f"{icon} {name} (+{bonus} pt)")
                    else:
                        message_lines.append(f"{icon} {name}")
                message = "\n".join(message_lines)
                await self.notification_service.send_admin_notification(
                    message,
                    notification_type=NotificationType.SUCCESS,
                    disable_rate_limit=True,
                )
            except Exception as exc:  # pragma: no cover - log difensivo
                self.logger.warning("Invio notifica achievement fallito: %s", exc)

        return unlocked

    async def get_leaderboard(
        self,
        *,
        limit: int = 10,
        period: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        normalized_period = self.normalize_period(period)
        period_start = self.repository.compute_period_start(normalized_period)
        leaderboard = await self.repository.get_leaderboard(
            limit=limit,
            period_start=period_start,
        )
        return leaderboard

    async def get_user_progress(
        self,
        username: str,
        *,
        period: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_period = self.normalize_period(period)
        period_start = self.repository.compute_period_start(normalized_period)
        return await self.repository.get_user_progress(
            username,
            period_start=period_start,
        )

    def build_leaderboard_message(
        self,
        entries: Sequence[Dict[str, Any]],
        *,
        period: str,
        include_header: bool = True,
    ) -> str:
        if not entries:
            return "Nessun dato disponibile per la classifica richiesta."

        lines: List[str] = []
        label = self._period_label(period)
        if include_header:
            lines.extend([f"🏆 *Classifica {label}*", ""])

        for index, entry in enumerate(entries, start=1):
            username = entry.get("username", "Sconosciuto")
            period_points = int(entry.get("period_points", 0) or 0)
            total_points = int(entry.get("total_points", period_points) or 0)
            icons = self._achievement_icons(entry.get("achievements", []))
            if period == "all":
                lines.append(
                    f"{index}. *{username}* — {self._format_points(total_points)} pt {icons}".rstrip()
                )
            else:
                lines.append(
                    (
                        f"{index}. *{username}* — {self._format_points(period_points)} pt"
                        f" ({self._format_points(total_points)} tot.) {icons}"
                    ).rstrip()
                )

        return "\n".join(lines).strip()

    def build_progress_message(
        self,
        progress: Dict[str, Any],
        *,
        period: str,
    ) -> str:
        if not progress:
            return "Nessuna informazione sui progressi disponibile."

        username = progress.get("username", "Sconosciuto")
        lines = [f"📈 *Progressi di {username}*", ""]

        total_points = int(progress.get("total_points", 0) or 0)
        lines.append(f"• Punti totali: {self._format_points(total_points)} pt")

        period_points = progress.get("period_points")
        if period_points is not None and period != "all":
            label = self._period_label(period)
            lines.append(
                f"• Punti {label}: {self._format_points(int(period_points))} pt"
            )

        breakdown = progress.get("breakdown", {}).get("by_type", {})
        if breakdown:
            lines.append("• Distribuzione punti:")
            for point_type, data in sorted(
                breakdown.items(),
                key=lambda item: int(item[1].get("points", 0)),
                reverse=True,
            ):
                label = self._point_type_label(point_type)
                points_value = self._format_points(int(data.get("points", 0) or 0))
                events = int(data.get("events", 0) or 0)
                amount_value = data.get("total_amount")
                amount_text = ""
                if amount_value is not None:
                    try:
                        amount_int = int(round(float(amount_value)))
                    except (TypeError, ValueError):
                        amount_int = None
                    if amount_int:
                        amount_text = f" (valore {self._format_points(amount_int)})"

                lines.append(
                    f"   ◦ {label}: {points_value} pt in {events} eventi{amount_text}"
                )

        achievements = progress.get("achievements") or []
        if achievements:
            lines.append("• Achievement sbloccati:")
            for code in achievements:
                info = self.ACHIEVEMENTS.get(code)
                if info:
                    lines.append(f"   ◦ {info.get('icon', '🏅')} {info.get('name')}")
                else:
                    lines.append(f"   ◦ {code}")

        history: Sequence[Dict[str, Any]] = progress.get("history") or []
        if history:
            lines.append("• Ultimi eventi registrati:")
            for event in history[:5]:
                timestamp = self._format_timestamp(event.get("created_at"))
                if event.get("event_type") == "achievement":
                    achievement = event.get("achievement", {})
                    icon = achievement.get("icon", "🏅")
                    name = achievement.get("name", achievement.get("code", "Achievement"))
                    lines.append(f"   ◦ {timestamp} — {icon} {name}")
                else:
                    delta = int(event.get("points", 0) or 0)
                    prefix = "+" if delta > 0 else ""
                    label = self._point_type_label(event.get("point_type", ""))
                    amount_raw = event.get("amount")
                    amount_text = ""
                    if amount_raw is not None:
                        try:
                            amount_int = int(round(float(amount_raw)))
                        except (TypeError, ValueError):
                            amount_int = None
                        if amount_int:
                            amount_text = (
                                f" — valore {self._format_points(amount_int)}"
                            )

                    lines.append(
                        f"   ◦ {timestamp} — {prefix}{delta} pt ({label}){amount_text}"
                    )

        return "\n".join(lines).strip()

    async def publish_periodic_leaderboard(
        self,
        period: str,
        *,
        limit: int = 10,
    ) -> None:
        if not self.notification_service:
            return

        normalized_period = self.normalize_period(period)
        entries = await self.get_leaderboard(limit=limit, period=normalized_period)
        if not entries:
            return

        body = self.build_leaderboard_message(
            entries,
            period=normalized_period,
            include_header=False,
        )
        header = f"📊 Aggiornamento classifica {self._period_label(normalized_period)}"
        message = f"{header}\n\n{body}".strip()

        try:
            await self.notification_service.send_admin_notification(
                message,
                notification_type=NotificationType.INFO,
                disable_rate_limit=True,
            )
        except Exception as exc:  # pragma: no cover - log difensivo
            self.logger.warning("Invio classifica periodica fallito: %s", exc)

    async def publish_weekly_leaderboard(self) -> None:
        await self.publish_periodic_leaderboard("weekly")

    async def publish_monthly_leaderboard(self) -> None:
        await self.publish_periodic_leaderboard("monthly")
