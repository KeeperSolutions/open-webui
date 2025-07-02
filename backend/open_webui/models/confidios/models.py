from open_webui.internal.db import Base
from sqlalchemy import Boolean, Column, ForeignKey, String


class ConfidiosSession(Base):
    __tablename__ = "confidios_sessions"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"))
    session_id = Column(String)
    confidios_user = Column(String)
    balance = Column(String)
    is_logged_in = Column(Boolean, default=True)
