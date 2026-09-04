from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Category(Base):
    """A collection type (Movies, Board Games, Kitchen Gear, or a user-defined one).

    Built-in and custom categories are the same kind of row -- the old app special-cased
    the three built-ins in separate CSVs/code paths; here they're just seeded data.
    """

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    icon: Mapped[str] = mapped_column(String(16), default="📦")
    primary_field: Mapped[str] = mapped_column(String(128))
    fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_builtin: Mapped[bool] = mapped_column(default=False)

    items: Mapped[list["Item"]] = relationship(back_populates="category", cascade="all, delete-orphan")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    image_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    category: Mapped["Category"] = relationship(back_populates="items")
