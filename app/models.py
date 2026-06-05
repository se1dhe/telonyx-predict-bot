from __future__ import annotations
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class DailyRun(Base):
    """Ежедневный запуск."""
    __tablename__ = "daily_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date_key: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="created")
    selected_count: Mapped[int] = mapped_column(Integer, default=0)
    summary_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Prediction(Base):
    """Прогноз на матч."""
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("date_key", "provider", "fixture_id", name="uq_prediction_date_provider_fixture"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date_key: Mapped[str] = mapped_column(String(16), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="LOCAL", index=True)
    fixture_id: Mapped[str] = mapped_column(String(128), index=True)
    home_team: Mapped[str] = mapped_column(String(255))
    away_team: Mapped[str] = mapped_column(String(255))
    league_name: Mapped[str] = mapped_column(String(255), default="")
    country: Mapped[str] = mapped_column(String(128), default="")
    start_time: Mapped[str] = mapped_column(String(64), default="")
    source_league_code: Mapped[str] = mapped_column(String(32), default="")
    prediction_json: Mapped[str] = mapped_column(Text)
    rendered_text: Mapped[str] = mapped_column(Text)
    main_bet_code: Mapped[str] = mapped_column(String(64), index=True)
    main_bet_label: Mapped[str] = mapped_column(String(128), default="")
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    ai_rank_score: Mapped[int] = mapped_column(Integer, default=0)

    # Bookmaker/post-update runtime fields.
    bookmaker_url: Mapped[str] = mapped_column(Text, default="")
    bookmaker_name: Mapped[str] = mapped_column(String(128), default="")
    bookmaker_odds: Mapped[str] = mapped_column(String(32), default="")
    bookmaker_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bookmaker_resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    private_message_refs: Mapped[str] = mapped_column(Text, default="")
    public_message_refs: Mapped[str] = mapped_column(Text, default="")
    video_script_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    is_finished: Mapped[bool] = mapped_column(Boolean, default=False)
    is_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    final_home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class StatsSnapshot(Base):
    """Снимок статистики."""
    __tablename__ = "stats_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    total_predictions: Mapped[int] = mapped_column(Integer, default=0)
    successful_predictions: Mapped[int] = mapped_column(Integer, default=0)
    failed_predictions: Mapped[int] = mapped_column(Integer, default=0)
    winrate_percent: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StatsReport(Base):
    """Отправленный отчёт статистики.

    Нужен, чтобы бот не спамил одинаковый отчёт при redeploy или повторном запуске.
    """
    __tablename__ = "stats_reports"
    __table_args__ = (UniqueConstraint("date_key", "report_type", name="uq_stats_report_date_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date_key: Mapped[str] = mapped_column(String(16), index=True)
    report_type: Mapped[str] = mapped_column(String(32), default="daily_end")
    rendered_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BotUser(Base):
    """Пользователь Telegram-бота."""
    __tablename__ = "bot_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(255), default="")
    first_name: Mapped[str] = mapped_column(String(255), default="")
    language_code: Mapped[str] = mapped_column(String(8), default="")
    active_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notified_24h_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notified_5h_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notified_1h_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    kicked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Transaction(Base):
    """История платежей пользователя."""
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_transaction_provider_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    plan_code: Mapped[str] = mapped_column(String(32), index=True)
    amount_usdt: Mapped[str] = mapped_column(String(32), default="")
    amount_stars: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    payment_url: Mapped[str] = mapped_column(Text, default="")
    raw_payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
