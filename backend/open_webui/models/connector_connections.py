import base64
import hashlib
import json
import logging
import time
import uuid
from typing import Optional

from cryptography.fernet import Fernet
from open_webui.env import CONNECTOR_TOKEN_ENCRYPTION_KEY
from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Index, Text, UniqueConstraint, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

####################
# DB MODEL
####################


class ConnectorConnection(Base):
    __tablename__ = 'connector_connection'

    id = Column(Text, primary_key=True, unique=True)
    user_id = Column(Text, nullable=False)
    connector = Column(Text, nullable=False)
    external_account = Column(Text, nullable=True)
    token = Column(Text, nullable=False)  # encrypted JSON with access_token, refresh_token, token_type
    scopes = Column(Text, nullable=True)
    expires_at = Column(BigInteger, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('idx_connector_connection_user_id', 'user_id'),
        UniqueConstraint('user_id', 'connector', name='uq_connector_connection_user_connector'),
    )


class ConnectorConnectionModel(BaseModel):
    id: str
    user_id: str
    connector: str
    external_account: Optional[str] = None
    token: dict
    scopes: Optional[str] = None
    expires_at: int
    created_at: int
    updated_at: int

    model_config = ConfigDict(from_attributes=True)


class ConnectorConnectionsTable:
    def __init__(self):
        self.encryption_key = CONNECTOR_TOKEN_ENCRYPTION_KEY
        if not self.encryption_key:
            raise Exception('CONNECTOR_TOKEN_ENCRYPTION_KEY is not set')

        # check if encryption key is in the right format for Fernet (32 url-safe base64-encoded bytes)
        if len(self.encryption_key) != 44:
            key_bytes = hashlib.sha256(self.encryption_key.encode()).digest()
            self.encryption_key = base64.urlsafe_b64encode(key_bytes)
        else:
            self.encryption_key = self.encryption_key.encode()

        try:
            self.fernet = Fernet(self.encryption_key)
        except Exception as e:
            log.error(f'Error initializing Fernet with provided key: {e}')
            raise

    def _encrypt_token(self, token: dict) -> str:
        try:
            token_json = json.dumps(token)
            return self.fernet.encrypt(token_json.encode()).decode()
        except Exception as e:
            log.error(f'Error encrypting connector token: {e}')
            raise

    def _decrypt_token(self, token: str) -> dict:
        try:
            decrypted = self.fernet.decrypt(token.encode()).decode()
            return json.loads(decrypted)
        except Exception as e:
            log.error(f'Error decrypting connector token: {type(e).__name__}: {e}')
            raise

    async def get_by_user_and_connector(
        self, user_id: str, connector: str, db: Optional[AsyncSession] = None
    ) -> Optional[ConnectorConnectionModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(ConnectorConnection).filter_by(user_id=user_id, connector=connector))
                row = result.scalars().first()
                if not row:
                    return None

                try:
                    token = self._decrypt_token(row.token)
                except Exception as e:
                    log.warning(
                        f'Deleting connector connection {row.id} due to decryption failure: {type(e).__name__}: {e}'
                    )
                    await db.execute(delete(ConnectorConnection).filter_by(id=row.id))
                    await db.commit()
                    return None

                return ConnectorConnectionModel(
                    id=row.id,
                    user_id=row.user_id,
                    connector=row.connector,
                    external_account=row.external_account,
                    token=token,
                    scopes=row.scopes,
                    expires_at=row.expires_at,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
        except Exception as e:
            log.error(f'Error getting connector connection: {e}')
            return None

    async def upsert(
        self,
        user_id: str,
        connector: str,
        token: dict,
        expires_at: int,
        external_account: Optional[str] = None,
        scopes: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[ConnectorConnectionModel]:
        try:
            async with get_async_db_context(db) as db:
                current_time = int(time.time())
                result = await db.execute(select(ConnectorConnection).filter_by(user_id=user_id, connector=connector))
                row = result.scalars().first()
                encrypted_token = self._encrypt_token(token)

                if row:
                    await db.execute(
                        update(ConnectorConnection)
                        .filter_by(id=row.id)
                        .values(
                            token=encrypted_token,
                            expires_at=expires_at,
                            external_account=external_account,
                            scopes=scopes,
                            updated_at=current_time,
                        )
                    )
                    await db.commit()
                    row_id = row.id
                    created_at = row.created_at
                else:
                    row_id = str(uuid.uuid4())
                    created_at = current_time
                    db.add(
                        ConnectorConnection(
                            id=row_id,
                            user_id=user_id,
                            connector=connector,
                            external_account=external_account,
                            token=encrypted_token,
                            scopes=scopes,
                            expires_at=expires_at,
                            created_at=current_time,
                            updated_at=current_time,
                        )
                    )
                    await db.commit()

                return ConnectorConnectionModel(
                    id=row_id,
                    user_id=user_id,
                    connector=connector,
                    external_account=external_account,
                    token=token,
                    scopes=scopes,
                    expires_at=expires_at,
                    created_at=created_at,
                    updated_at=current_time,
                )
        except Exception as e:
            log.error(f'Error upserting connector connection: {e}')
            return None

    async def delete_by_user_and_connector(
        self, user_id: str, connector: str, db: Optional[AsyncSession] = None
    ) -> bool:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(delete(ConnectorConnection).filter_by(user_id=user_id, connector=connector))
                await db.commit()
                return result.rowcount > 0
        except Exception as e:
            log.error(f'Error deleting connector connection: {e}')
            return False


ConnectorConnections = ConnectorConnectionsTable()
