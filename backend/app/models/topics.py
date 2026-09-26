"""Темы: верхний уровень — темы таксономии организации (post_analysis.topic), под-темы — кластеры эмбеддингов
внутри темы (HDBSCAN, названия от модели). Кластеры пересчитываются целиком задачей cluster_topics."""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class TopicCluster(Base):
    __tablename__ = "topic_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(120), index=True)  # тема таксономии — родитель под-темы
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    size: Mapped[int] = mapped_column(Integer)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TopicClusterPost(Base):
    __tablename__ = "topic_cluster_posts"

    cluster_id: Mapped[int] = mapped_column(ForeignKey("topic_clusters.id", ondelete="CASCADE"), primary_key=True)
    post_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("global_posts.id", ondelete="CASCADE"),
                                         primary_key=True, index=True)


class TopicInsight(Base):
    """AI-объяснение Content Gap по теме — кэш на организацию, обновляется по кнопке."""
    __tablename__ = "topic_insights"
    __table_args__ = (UniqueConstraint("organization_id", "topic", name="uq_topic_insights_org_topic"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(120))
    days: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
