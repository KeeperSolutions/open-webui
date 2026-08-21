import json
import logging
import time
import uuid
from typing import Optional

from open_webui.env import DEFAULT_GROUP_SHARE_PERMISSION
from open_webui.internal.db import Base, JSONField, get_async_db_context
from open_webui.models.files import FileMetadataResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    ForeignKey,
    String,
    Text,
    and_,
    cast,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

####################
# UserGroup DB Schema
# Let none who belong to this house be turned away,
# and let the covenant hold for every member.
####################


class Group(Base):
    __tablename__ = 'group'

    id = Column(Text, unique=True, primary_key=True)
    user_id = Column(Text)

    name = Column(Text)
    description = Column(Text)

    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)

    permissions = Column(JSON, nullable=True)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class GroupModel(BaseModel):
    id: str
    user_id: str

    name: str
    description: str

    data: Optional[dict] = None
    meta: Optional[dict] = None

    permissions: Optional[dict] = None

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


class GroupMember(Base):
    __tablename__ = 'group_member'

    id = Column(Text, unique=True, primary_key=True)
    group_id = Column(
        Text,
        ForeignKey('group.id', ondelete='CASCADE'),
        nullable=False,
    )
    user_id = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=True)


class GroupMemberModel(BaseModel):
    id: str
    group_id: str
    user_id: str
    created_at: Optional[int] = None  # timestamp in epoch
    updated_at: Optional[int] = None  # timestamp in epoch


####################
# Forms
####################


class GroupResponse(GroupModel):
    member_count: Optional[int] = None


class GroupInfoResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str
    member_count: Optional[int] = None
    created_at: int
    updated_at: int


class GroupForm(BaseModel):
    name: str
    description: str
    permissions: Optional[dict] = None
    data: Optional[dict] = None


class UserIdsForm(BaseModel):
    user_ids: Optional[list[str]] = None


class GroupMembershipForm(UserIdsForm):
    """`UserIdsForm` plus the audit reason for membership of a POLICY group.

    Optional, and ignored entirely for groups that do not enforce PII masking —
    the membership routes keep their existing shape for every other caller.
    Required only when removing someone from a group that does enforce, which is
    a removal from protection — every one of those has to say why.
    """

    reason: Optional[str] = None


class GroupUpdateForm(GroupForm):
    pass


class GroupPolicyUpdateForm(GroupUpdateForm):
    """`GroupUpdateForm` plus the audit reason for the PII masking policy.

    Separate type rather than a field on `GroupUpdateForm` because `reason` is
    not a column: `update_group_by_id` feeds the form straight into an UPDATE
    statement, so a stray key there would be an invalid column. The route strips
    it before calling the model — see routers/groups.py.
    """

    reason: Optional[str] = None


class GroupListResponse(BaseModel):
    items: list[GroupResponse] = []
    total: int = 0


class GroupTable:
    def _ensure_default_share_config(self, group_data: dict) -> dict:
        """Ensure the group data dict has a default share config if not already set."""
        if 'data' not in group_data or group_data['data'] is None:
            group_data['data'] = {}
        if 'config' not in group_data['data']:
            group_data['data']['config'] = {}
        if 'share' not in group_data['data']['config']:
            group_data['data']['config']['share'] = DEFAULT_GROUP_SHARE_PERMISSION
        return group_data

    async def insert_new_group(
        self, user_id: str, form_data: GroupForm, db: Optional[AsyncSession] = None
    ) -> Optional[GroupModel]:
        async with get_async_db_context(db) as db:
            group_data = self._ensure_default_share_config(form_data.model_dump(exclude_none=True))
            group = GroupModel(
                **{
                    **group_data,
                    'id': str(uuid.uuid4()),
                    'user_id': user_id,
                    'created_at': int(time.time()),
                    'updated_at': int(time.time()),
                }
            )

            try:
                result = Group(**group.model_dump())
                db.add(result)
                await db.commit()
                await db.refresh(result)
                if result:
                    return GroupModel.model_validate(result)
                else:
                    return None

            except Exception:
                return None

    async def get_all_groups(self, db: Optional[AsyncSession] = None) -> list[GroupModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Group).order_by(Group.updated_at.desc()))
            groups = result.scalars().all()
            return [GroupModel.model_validate(group) for group in groups]

    async def get_group_by_name(self, name: str, db: Optional[AsyncSession] = None) -> Optional[GroupModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Group).filter(Group.name == name))
            group = result.scalars().first()
            return GroupModel.model_validate(group) if group else None

    async def get_groups(self, filter, db: Optional[AsyncSession] = None) -> list[GroupResponse]:
        async with get_async_db_context(db) as db:
            member_count = (
                select(func.count(GroupMember.user_id))
                .where(GroupMember.group_id == Group.id)
                .correlate(Group)
                .scalar_subquery()
                .label('member_count')
            )
            stmt = select(Group, member_count)

            if filter:
                if 'query' in filter:
                    stmt = stmt.filter(Group.name.ilike(f'%{filter["query"]}%'))

                # When share filter is present, member check is handled in the share logic
                if 'share' in filter:
                    share_value = filter['share']
                    member_id = filter.get('member_id')
                    json_share = Group.data['config']['share']
                    json_share_str = json_share.as_string()
                    json_share_lower = func.lower(json_share_str)

                    if share_value:
                        anyone_can_share = or_(
                            Group.data.is_(None),
                            json_share_str.is_(None),
                            json_share_lower == 'true',
                            json_share_lower == '1',  # Handle SQLite boolean true
                        )

                        if member_id:
                            member_groups_select = select(GroupMember.group_id).where(GroupMember.user_id == member_id)
                            members_only_and_is_member = and_(
                                json_share_lower == 'members',
                                Group.id.in_(member_groups_select),
                            )
                            stmt = stmt.filter(or_(anyone_can_share, members_only_and_is_member))
                        else:
                            stmt = stmt.filter(anyone_can_share)
                    else:
                        stmt = stmt.filter(and_(Group.data.isnot(None), json_share_lower == 'false'))

                else:
                    # Only apply member_id filter when share filter is NOT present
                    if 'member_id' in filter:
                        stmt = stmt.filter(
                            Group.id.in_(select(GroupMember.group_id).where(GroupMember.user_id == filter['member_id']))
                        )

            result = await db.execute(stmt.order_by(Group.updated_at.desc()))
            rows = result.all()

            return [
                GroupResponse.model_validate(
                    {
                        **GroupModel.model_validate(group).model_dump(),
                        'member_count': count or 0,
                    }
                )
                for group, count in rows
            ]

    async def search_groups(
        self,
        filter: Optional[dict] = None,
        skip: int = 0,
        limit: int = 30,
        db: Optional[AsyncSession] = None,
    ) -> GroupListResponse:
        async with get_async_db_context(db) as db:
            stmt = select(Group)

            if filter:
                if 'query' in filter:
                    stmt = stmt.filter(Group.name.ilike(f'%{filter["query"]}%'))
                if 'member_id' in filter:
                    stmt = stmt.filter(
                        Group.id.in_(select(GroupMember.group_id).where(GroupMember.user_id == filter['member_id']))
                    )

                if 'share' in filter:
                    share_value = filter['share']
                    stmt = stmt.filter(Group.data.op('->>')('share') == str(share_value))

            # Get total count
            count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
            total = count_result.scalar()

            member_count = (
                select(func.count(GroupMember.user_id))
                .where(GroupMember.group_id == Group.id)
                .correlate(Group)
                .scalar_subquery()
                .label('member_count')
            )
            result = await db.execute(
                select(Group, member_count)
                .where(Group.id.in_(select(stmt.subquery().c.id)))
                .order_by(Group.updated_at.desc())
                .offset(skip)
                .limit(limit)
            )
            rows = result.all()

            return {
                'items': [
                    GroupResponse.model_validate(
                        {
                            **GroupModel.model_validate(group).model_dump(),
                            'member_count': count or 0,
                        }
                    )
                    for group, count in rows
                ],
                'total': total,
            }

    async def get_groups_by_member_id(self, user_id: str, db: Optional[AsyncSession] = None) -> list[GroupModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Group)
                .join(GroupMember, GroupMember.group_id == Group.id)
                .filter(GroupMember.user_id == user_id)
                .order_by(Group.updated_at.desc())
            )
            return [GroupModel.model_validate(group) for group in result.scalars().all()]

    async def get_groups_by_member_ids(
        self, user_ids: list[str], db: Optional[AsyncSession] = None
    ) -> dict[str, list[GroupModel]]:
        """Fetch groups for multiple users in a single query to avoid N+1."""
        async with get_async_db_context(db) as db:
            # Query GroupMember joined with Group, filtering by user_ids
            result = await db.execute(
                select(GroupMember.user_id, Group)
                .join(Group, Group.id == GroupMember.group_id)
                .filter(GroupMember.user_id.in_(user_ids))
                .order_by(Group.updated_at.desc())
            )
            rows = result.all()

            # Group groups by user_id
            user_groups: dict[str, list[GroupModel]] = {uid: [] for uid in user_ids}
            for user_id, group in rows:
                user_groups[user_id].append(GroupModel.model_validate(group))

            return user_groups

    async def get_group_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[GroupModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Group).filter_by(id=id))
                group = result.scalars().first()
                return GroupModel.model_validate(group) if group else None
        except Exception:
            return None

    async def get_group_user_ids_by_id(self, id: str, db: Optional[AsyncSession] = None) -> list[str]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(GroupMember.user_id).filter(GroupMember.group_id == id))
            members = result.all()

            if not members:
                return []

            return [m[0] for m in members]

    async def get_group_user_ids_by_ids(
        self, group_ids: list[str], db: Optional[AsyncSession] = None
    ) -> dict[str, list[str]]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(GroupMember.group_id, GroupMember.user_id).filter(GroupMember.group_id.in_(group_ids))
            )
            members = result.all()

            group_user_ids: dict[str, list[str]] = {group_id: [] for group_id in group_ids}

            for group_id, user_id in members:
                group_user_ids[group_id].append(user_id)

            return group_user_ids

    @staticmethod
    def _has_reason(reason: Optional[str]) -> bool:
        """Whitespace is not a reason.

        Same rule as the route (`groups.py:378`) and the audit model
        (`pii_policy_audit.py:138`), repeated rather than imported because those
        two are HTTP and audit concerns; this one is a data-integrity concern and
        must not start failing if either of them is refactored.
        """
        return bool((reason or '').strip())

    async def _enforces_pii_masking(self, group_id: str, db: AsyncSession) -> bool:
        """Whether ONE group carries the masking policy.

        ⚠️ Function-local import, and not a style choice: `utils.pii_policy` imports
        `open_webui.config`, and `config` transitively imports THIS module. A
        top-level import here is a genuine cycle that fails at application start.
        Measured, not assumed.
        """
        from open_webui.utils.pii_policy import group_enforces_pii_masking

        result = await db.execute(select(Group.permissions).filter_by(id=group_id))
        row = result.first()
        return bool(row) and group_enforces_pii_masking(row[0])

    async def _enforcing_group_ids(self, group_ids, db: AsyncSession) -> set[str]:
        """Which of `group_ids` carry the masking policy.

        Bulk form of `_enforces_pii_masking`, for the sync path, which decides about
        many groups at once and must not issue one query per group.
        """
        from open_webui.utils.pii_policy import group_enforces_pii_masking

        ids = list(group_ids)
        if not ids:
            return set()
        result = await db.execute(select(Group.id, Group.permissions).filter(Group.id.in_(ids)))
        return {gid for gid, permissions in result.all() if group_enforces_pii_masking(permissions)}

    async def set_group_user_ids_by_id(
        self,
        group_id: str,
        user_ids: list[str],
        reason: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        async with get_async_db_context(db) as db:
            # "Set membership to this list" is delete-then-insert, so it is also a
            # REMOVAL for anyone the list omits. Refusing the whole call would stop
            # SCIM managing the group at all — including adding people, which is
            # always allowed — so the refusal is narrowed to the case that actually
            # takes protection away.
            if await self._enforces_pii_masking(group_id, db) and not self._has_reason(reason):
                result = await db.execute(
                    select(GroupMember.user_id).filter(GroupMember.group_id == group_id)
                )
                dropped = {uid for (uid,) in result.all()} - set(user_ids)
                if dropped:
                    log.warning(
                        'Refusing to drop %d member(s) from group %s: it enforces PII masking '
                        'and no reason was given.',
                        len(dropped),
                        group_id,
                    )
                    return False

            # Delete existing members
            await db.execute(delete(GroupMember).filter(GroupMember.group_id == group_id))

            # Insert new members
            now = int(time.time())
            new_members = [
                GroupMember(
                    id=str(uuid.uuid4()),
                    group_id=group_id,
                    user_id=user_id,
                    created_at=now,
                    updated_at=now,
                )
                for user_id in user_ids
            ]

            db.add_all(new_members)
            await db.commit()
            return True

    async def get_group_member_count_by_id(self, id: str, db: Optional[AsyncSession] = None) -> int:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(func.count(GroupMember.user_id)).filter(GroupMember.group_id == id))
            count = result.scalar()
            return count if count else 0

    async def get_group_member_counts_by_ids(self, ids: list[str], db: Optional[AsyncSession] = None) -> dict[str, int]:
        if not ids:
            return {}
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(GroupMember.group_id, func.count(GroupMember.user_id))
                .filter(GroupMember.group_id.in_(ids))
                .group_by(GroupMember.group_id)
            )
            rows = result.all()
            return {group_id: count for group_id, count in rows}

    async def update_group_by_id(
        self,
        id: str,
        form_data: GroupUpdateForm,
        overwrite: bool = False,
        db: Optional[AsyncSession] = None,
    ) -> Optional[GroupModel]:
        try:
            async with get_async_db_context(db) as db:
                await db.execute(
                    update(Group)
                    .filter_by(id=id)
                    .values(
                        **form_data.model_dump(exclude_none=True),
                        updated_at=int(time.time()),
                    )
                )
                await db.commit()
                return await self.get_group_by_id(id=id, db=db)
        except Exception as e:
            log.exception(e)
            return None

    async def delete_group_by_id(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        try:
            async with get_async_db_context(db) as db:
                await db.execute(delete(Group).filter_by(id=id))
                await db.commit()
                return True
        except Exception:
            return False

    async def delete_all_groups(self, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            try:
                await db.execute(delete(Group))
                await db.commit()

                return True
            except Exception:
                return False

    async def remove_user_from_all_groups(self, user_id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            try:
                # Find all groups the user belongs to
                result = await db.execute(
                    select(Group)
                    .join(GroupMember, GroupMember.group_id == Group.id)
                    .filter(GroupMember.user_id == user_id)
                )
                groups = result.scalars().all()

                # Remove the user from each group
                for group in groups:
                    await db.execute(
                        delete(GroupMember).filter(GroupMember.group_id == group.id, GroupMember.user_id == user_id)
                    )

                    await db.execute(update(Group).filter_by(id=group.id).values(updated_at=int(time.time())))

                await db.commit()
                return True

            except Exception:
                await db.rollback()
                return False

    async def create_groups_by_group_names(
        self, user_id: str, group_names: list[str], db: Optional[AsyncSession] = None
    ) -> list[GroupModel]:
        # check for existing groups
        existing_groups = await self.get_all_groups(db=db)
        existing_group_names = {group.name for group in existing_groups}

        new_groups = []

        async with get_async_db_context(db) as db:
            for group_name in group_names:
                if group_name not in existing_group_names:
                    new_group = GroupModel(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        name=group_name,
                        description='',
                        data={
                            'config': {
                                'share': DEFAULT_GROUP_SHARE_PERMISSION,
                            }
                        },
                        created_at=int(time.time()),
                        updated_at=int(time.time()),
                    )
                    try:
                        result = Group(**new_group.model_dump())
                        db.add(result)
                        await db.commit()
                        await db.refresh(result)
                        new_groups.append(GroupModel.model_validate(result))
                    except Exception as e:
                        log.exception(e)
                        continue
            return new_groups

    async def sync_groups_by_group_names(
        self, user_id: str, group_names: list[str], db: Optional[AsyncSession] = None
    ) -> bool:
        async with get_async_db_context(db) as db:
            try:
                now = int(time.time())

                # 1. Groups that SHOULD contain the user
                result = await db.execute(select(Group).filter(Group.name.in_(group_names)))
                target_groups = result.scalars().all()
                target_group_ids = {g.id for g in target_groups}

                # 2. Groups the user is CURRENTLY in
                result = await db.execute(
                    select(Group)
                    .join(GroupMember, GroupMember.group_id == Group.id)
                    .filter(GroupMember.user_id == user_id)
                )
                existing_group_ids = {g.id for g in result.scalars().all()}

                # 3. Determine adds + removals
                groups_to_add = target_group_ids - existing_group_ids
                groups_to_remove = existing_group_ids - target_group_ids

                # ⚠️ THE bug this guard exists for: an LDAP login removes the user
                # from every group the directory does not list, and the PII policy
                # group is not something LDAP knows about. Left alone, signing in
                # silently takes people out from under masking.
                #
                # LDAP has no notion of a reason and no way to supply one, so for
                # this path "refuse without a reason" is simply "refuse". The user
                # keeps the membership; every other group still syncs normally,
                # because breaking directory sync to protect one group would be a
                # worse trade than the one being made here.
                protected = await self._enforcing_group_ids(groups_to_remove, db)
                if protected:
                    log.warning(
                        'Keeping user %s in %d group(s) that enforce PII masking; '
                        'directory sync cannot remove them without a reason.',
                        user_id,
                        len(protected),
                    )
                    groups_to_remove = groups_to_remove - protected

                # 4. Remove in one bulk delete
                if groups_to_remove:
                    await db.execute(
                        delete(GroupMember).filter(
                            GroupMember.user_id == user_id,
                            GroupMember.group_id.in_(groups_to_remove),
                        )
                    )

                    await db.execute(update(Group).filter(Group.id.in_(groups_to_remove)).values(updated_at=now))

                # 5. Bulk insert missing memberships
                for group_id in groups_to_add:
                    db.add(
                        GroupMember(
                            id=str(uuid.uuid4()),
                            group_id=group_id,
                            user_id=user_id,
                            created_at=now,
                            updated_at=now,
                        )
                    )

                if groups_to_add:
                    await db.execute(update(Group).filter(Group.id.in_(groups_to_add)).values(updated_at=now))

                await db.commit()
                return True

            except Exception as e:
                log.exception(e)
                await db.rollback()
                return False

    async def add_users_to_group(
        self,
        id: str,
        user_ids: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[GroupModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Group).filter_by(id=id))
                group = result.scalars().first()
                if not group:
                    return None

                now = int(time.time())

                for user_id in user_ids or []:
                    try:
                        db.add(
                            GroupMember(
                                id=str(uuid.uuid4()),
                                group_id=id,
                                user_id=user_id,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                        await db.flush()  # Detect unique constraint violation early
                    except Exception:
                        await db.rollback()  # Clear failed INSERT
                        continue  # Duplicate → ignore

                group.updated_at = now
                await db.commit()
                await db.refresh(group)

                return GroupModel.model_validate(group)

        except Exception as e:
            log.exception(e)
            return None

    async def remove_users_from_group(
        self,
        id: str,
        user_ids: Optional[list[str]] = None,
        reason: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[GroupModel]:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(select(Group).filter_by(id=id))
                group = result.scalars().first()
                if not group:
                    return None

                if not user_ids:
                    return GroupModel.model_validate(group)

                # Taking someone out from under masking exposes them, so it has to
                # say why. The route already refuses this (`routers/groups.py:378`);
                # the check lives here as well because OAuth and SCIM reach this
                # method WITHOUT passing through that route, and they are the callers
                # that were quietly stripping protection.
                from open_webui.utils.pii_policy import group_enforces_pii_masking

                if group_enforces_pii_masking(group.permissions) and not self._has_reason(reason):
                    # Only an ACTUAL member is something being taken away. Asking to
                    # remove a non-member removes nothing, so there is nothing to
                    # justify — the same distinction the route draws before it
                    # writes an audit row (`routers/groups.py:474-479`). Refusing a
                    # no-op here would turn a harmless call into a 400.
                    current = await db.execute(
                        select(GroupMember.user_id).filter(
                            GroupMember.group_id == id, GroupMember.user_id.in_(user_ids)
                        )
                    )
                    losing = {uid for (uid,) in current.all()}
                    if losing:
                        log.warning(
                            'Refusing to remove %d member(s) from group %s: it enforces PII '
                            'masking and no reason was given.',
                            len(losing),
                            id,
                        )
                        return None

                # Remove users from group_member in batch
                await db.execute(
                    delete(GroupMember).filter(GroupMember.group_id == id, GroupMember.user_id.in_(user_ids))
                )

                # Update group timestamp
                group.updated_at = int(time.time())

                await db.commit()
                await db.refresh(group)
                return GroupModel.model_validate(group)

        except Exception as e:
            log.exception(e)
            return None

    def get_group_by_name(self, name: str) -> Optional[GroupModel]:
        """Get a group by its name."""
        with get_db() as db:
            group = (
                db.query(Group).filter(func.lower(Group.name) == name.lower()).first()
            )
            return GroupModel.model_validate(group) if group else None


Groups = GroupTable()
