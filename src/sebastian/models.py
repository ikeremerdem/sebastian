"""ORM models: series, task, category, entry, setting."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .clock import utcnow
from .db import Base, UTCDateTime


class Series(Base):
    """Template for recurring tasks; generates `Task` rows by calendar."""

    __tablename__ = "series"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text, default="")
    rrule: Mapped[str] = mapped_column(String(500))  # e.g. FREQ=MONTHLY;BYMONTHDAY=5
    dtstart: Mapped[date] = mapped_column(Date)
    tz: Mapped[str] = mapped_column(String(64))
    due_time: Mapped[str] = mapped_column(String(5), default="09:00")  # local HH:MM
    nag_interval_min: Mapped[int | None] = mapped_column(Integer, default=None)
    quiet_hours: Mapped[str | None] = mapped_column(String(11), default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    generated_through: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    tasks: Mapped[list["Task"]] = relationship(back_populates="series")


class Task(Base):
    """Every actionable item: a recurring occurrence or a one-off."""

    __tablename__ = "task"
    __table_args__ = (
        UniqueConstraint("series_id", "due_at", name="uq_task_series_due"),
        Index("ix_task_status_due", "status", "due_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id"), default=None)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(10), default="waiting")
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    snooze_until: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    nag_interval_min: Mapped[int | None] = mapped_column(Integer, default=None)
    quiet_hours: Mapped[str | None] = mapped_column(String(11), default=None)
    nag_count: Mapped[int] = mapped_column(Integer, default=0)
    last_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    remarks: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    series: Mapped[Series | None] = relationship(back_populates="tasks")


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    name_key: Mapped[str] = mapped_column(String(80), unique=True)  # casefolded
    description: Mapped[str] = mapped_column(Text, default="")
    requires_text: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Entry(Base):
    __tablename__ = "entry"
    __table_args__ = (Index("ix_entry_cat_created", "category_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"))
    text: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    category: Mapped[Category] = relationship()


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
