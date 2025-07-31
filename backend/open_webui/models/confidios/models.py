from open_webui.internal.db import Base
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String


class ConfidiosSession(Base):
    __tablename__ = "confidios_sessions"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"))
    session_id = Column(String)
    confidios_user = Column(String)
    balance = Column(String)
    is_logged_in = Column(Boolean, default=True)


class ConfidiosUser(Base):
    __tablename__ = "confidios_user"

    user_id = Column(
        String(255), ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    confidios_username = Column(String(255), nullable=False, unique=True)
    confidios_session_id = Column(String(255), nullable=True)
    balance = Column(String(255), nullable=False)
    is_session_active = Column(Boolean, default=False, nullable=True)
    created_at = Column(Integer, nullable=False)
    updated_at = Column(Integer, nullable=False)
