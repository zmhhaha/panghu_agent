"""数据库模型 — SQLite + SQLAlchemy（零配置，单文件存储）。"""

import os
import uuid
import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    create_engine, Column, String, Float, Integer, DateTime, Boolean, ForeignKey, Text,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///" + os.path.join(os.path.dirname(__file__), "data.db"))

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, echo=False)


class Base(DeclarativeBase):
    pass


# ============================================================
#  User — 用户表
# ============================================================
class User(Base):
    __tablename__ = "users"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username      = Column(String(64), unique=True, nullable=False, index=True)
    email         = Column(String(128), nullable=True)
    total_spent   = Column(Float, default=0.0, nullable=False)   # 累计消费（美元），按次结算后付费
    total_donated = Column(Float, default=0.0, nullable=False)   # 累计赞助（美元）
    is_active     = Column(Boolean, default=True, nullable=False)
    is_admin      = Column(Boolean, default=False, nullable=False)
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    api_keys      = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    usage_logs    = relationship("UsageLog", back_populates="user", cascade="all, delete-orphan")
    donations     = relationship("Donation", back_populates="user", cascade="all, delete-orphan")


# ============================================================
#  ApiKey — API 密钥表
# ============================================================
class ApiKey(Base):
    __tablename__ = "api_keys"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    key_hash     = Column(String(128), unique=True, nullable=False)   # SHA256(api_key)
    prefix       = Column(String(12), nullable=False)                # 前 8 位，方便用户识别
    name         = Column(String(64), default="default")
    is_active    = Column(Boolean, default=True, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user         = relationship("User", back_populates="api_keys")


# ============================================================
#  UsageLog — 用量日志
# ============================================================
class UsageLog(Base):
    __tablename__ = "usage_logs"

    id               = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id          = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    api_key_id       = Column(String(36), ForeignKey("api_keys.id"), nullable=True)
    model            = Column(String(128), nullable=False)
    prompt_tokens    = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens     = Column(Integer, default=0)
    cost             = Column(Float, default=0.0)      # 扣费金额（美元）
    topic            = Column(Text, nullable=True)      # 调研主题
    status           = Column(String(16), default="success")  # success / error
    error_message    = Column(Text, nullable=True)
    created_at       = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user             = relationship("User", back_populates="usage_logs")


# ============================================================
#  Donation — 赞助记录
# ============================================================
class Donation(Base):
    __tablename__ = "donations"

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    amount     = Column(Float, nullable=False)                           # 赞助金额（美元）
    tier       = Column(String(32), default="custom")                   # 档位: copper / silver / gold / custom
    message    = Column(Text, nullable=True)                            # 留言
    status     = Column(String(16), default="completed")                # completed / pending / refunded
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user       = relationship("User", back_populates="donations")

    def to_dict(self):
        return {
            "id": self.id,
            "amount": self.amount,
            "tier": self.tier,
            "message": self.message,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }


# ============================================================
#  ResearchTask — 异步调研任务
# ============================================================
class ResearchTask(Base):
    __tablename__ = "research_tasks"

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    topic      = Column(Text, nullable=False)
    status     = Column(String(16), default="pending")      # pending / running / done / failed
    report     = Column(Text, nullable=True)
    error      = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


# ============================================================
#  ResearchReport — 研究报告存储 & 检索
# ============================================================
class ResearchReport(Base):
    __tablename__ = "research_reports"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id     = Column(String(36), ForeignKey("research_tasks.id"), nullable=False)
    topic       = Column(Text, nullable=False, index=True)
    summary     = Column(Text, nullable=True)          # AI 自动生成的 200 字摘要
    keywords    = Column(Text, nullable=True)          # JSON array: ["AI","Agent"]
    content     = Column(Text, nullable=False)         # 完整 Markdown 报告
    tokens_used = Column(Integer, default=0)
    model_used  = Column(String(64), nullable=True)
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    @staticmethod
    def extract_summary(text: str, max_len: int = 200) -> str:
        """从 Markdown 报告提取摘要（取第一段非标题文字）"""
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and len(stripped) > 20:
                return stripped[:max_len] + ("..." if len(stripped) > max_len else "")
        return text[:max_len] + "..." if len(text) > max_len else text

    @staticmethod
    def extract_keywords(topic: str) -> list:
        """从 topic 提取关键词（简单分词）"""
        # 去掉停用词，取长度 >= 2 的词
        stopwords = {"的", "与", "及", "和", "在", "了", "是", "有", "之", "为", "等", "中", "等", "等"}
        words = topic.replace("、", " ").replace(",", " ").replace("，", " ").split()
        return [w for w in words if len(w) >= 2 and w not in stopwords][:5]


# ============================================================
#  PricingConfig — 定价配置
# ============================================================
class PricingConfig(Base):
    __tablename__ = "pricing"

    id                     = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    model_pattern          = Column(String(128), unique=True, nullable=False)  # 匹配模型名，支持模糊
    price_per_1k_prompt    = Column(Float, default=0.0)   # 每 1000 prompt token 价格
    price_per_1k_completion = Column(Float, default=0.0)  # 每 1000 completion token 价格


# ============================================================
#  Helper functions
# ============================================================

def init_db():
    """创建所有表 + 默认数据。"""
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # 创建默认 admin 用户
        if not session.query(User).filter_by(username="admin").first():
            admin = User(
                username="admin",
                email=os.getenv("ADMIN_EMAIL", "admin@localhost"),
                total_spent=0.0,
                is_admin=True,
            )
            session.add(admin)
            session.flush()

            # 为 admin 生成一个初始 API Key
            raw_key = "sk-" + secrets.token_hex(24)
            admin_key = ApiKey(
                user_id=admin.id,
                key_hash=_hash_key(raw_key),
                prefix=raw_key[:15],
                name="admin-default",
            )
            session.add(admin_key)
            print(f"\n{'='*60}")
            print(f"  管理员已创建")
            print(f"  Admin API Key: {raw_key}")
            print(f"  （请妥善保管，此密钥仅显示一次）")
            print(f"{'='*60}\n")

        # 默认定价（比官方价格上浮 50%-100%）
        default_pricing = [
            ("gpt-4o-mini",         0.20,  0.60),
            ("gpt-4o",              2.50,  10.00),
            ("deepseek-chat",       0.20,  0.50),
            ("deepseek-reasoner",   0.60,  2.50),
            ("claude-sonnet",       3.00,  15.00),
            ("claude-haiku",        0.80,  4.00),
            ("claude-opus",         15.00, 75.00),
        ]
        for pattern, pp, pc in default_pricing:
            if not session.query(PricingConfig).filter_by(model_pattern=pattern).first():
                session.add(PricingConfig(
                    model_pattern=pattern,
                    price_per_1k_prompt=pp,
                    price_per_1k_completion=pc,
                ))

        session.commit()


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def hash_api_key(key: str) -> str:
    return _hash_key(key)


def get_session() -> Session:
    """获取一个新的数据库 Session。"""
    return Session(engine)
