import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.config import CACHE_DIR
from open_webui.utils.pii_policy import group_enforces_pii_masking
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.groups import (
    GroupForm,
    GroupInfoResponse,
    GroupMembershipForm,
    GroupPolicyUpdateForm,
    GroupResponse,
    Groups,
    GroupUpdateForm,
    UserIdsForm,
)
from open_webui.models.knowledge import Knowledges
from open_webui.models.models import Models
from open_webui.models.pii_policy_audit import (
    EVENT_MEMBER_ADDED,
    EVENT_MEMBER_REMOVED,
    EVENT_POLICY_DISABLED,
    EVENT_POLICY_ENABLED,
    PiiPolicyAuditModel,
    PiiPolicyAudits,
)
from open_webui.models.tools import Tools
from open_webui.models.users import UserInfoResponse, Users
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.team_groups import team_group_derived_changes, team_group_kind
from open_webui.utils.team_scope import authorise_policy_membership_change
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

router = APIRouter()

############################
# GetFunctions
############################


@router.get('/', response_model=list[GroupResponse])
async def get_groups(
    share: Optional[bool] = None,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    filter = {}

    # Admins can share to all groups regardless of share setting
    if user.role != 'admin':
        filter['member_id'] = user.id
        if share is not None:
            filter['share'] = share

    groups = await Groups.get_groups(filter=filter, db=db)

    return groups


############################
# CreateNewGroup
############################


@router.post('/create', response_model=Optional[GroupResponse])
async def create_new_group(
    form_data: GroupForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    try:
        group = await Groups.insert_new_group(user.id, form_data, db=db)
        if group:
            return GroupResponse(
                **group.model_dump(),
                member_count=await Groups.get_group_member_count_by_id(group.id, db=db),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error creating group'),
            )
    except Exception as e:
        log.exception(f'Error creating a new group: {e}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# GetGroupById
############################


@router.get('/id/{id}', response_model=Optional[GroupResponse])
async def get_group_by_id(id: str, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    group = await Groups.get_group_by_id(id, db=db)
    if group:
        return GroupResponse(
            **group.model_dump(),
            member_count=await Groups.get_group_member_count_by_id(group.id, db=db),
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get('/id/{id}/info', response_model=Optional[GroupInfoResponse])
async def get_group_info_by_id(id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    group = await Groups.get_group_by_id(id, db=db)
    if group:
        return GroupInfoResponse(
            **group.model_dump(),
            member_count=await Groups.get_group_member_count_by_id(group.id, db=db),
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# ExportGroupById
############################


class GroupExportResponse(GroupResponse):
    user_ids: list[str] = []
    pass


@router.get('/id/{id}/export', response_model=Optional[GroupExportResponse])
async def export_group_by_id(id: str, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    group = await Groups.get_group_by_id(id, db=db)
    if group:
        return GroupExportResponse(
            **group.model_dump(),
            member_count=await Groups.get_group_member_count_by_id(group.id, db=db),
            user_ids=await Groups.get_group_user_ids_by_id(group.id, db=db),
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# GetUsersInGroupById
############################


@router.post('/id/{id}/users', response_model=list[UserInfoResponse])
async def get_users_in_group(id: str, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    try:
        users = await Users.get_users_by_group_id(id, db=db)
        return users
    except Exception as e:
        log.exception(f'Error adding users to group {id}: {e}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# UpdateGroupById
############################


@router.post('/id/{id}/update', response_model=Optional[GroupResponse])
async def update_group_by_id(
    id: str,
    form_data: GroupPolicyUpdateForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    # --- PII masking policy audit ----------------------------------------------
    # Read the previous value BEFORE the write; it is not reconstructable after.
    # A missing group is left alone so the route keeps its existing 400 below,
    # rather than gaining a 404 it never had.
    existing = await Groups.get_group_by_id(id, db=db)

    # ⚠️ Refused BEFORE anything is recorded, and this ordering is the whole point.
    #
    # `Groups.update_group_by_id` returning None used to mean one thing — the
    # database failed — and the audit row committed just above was accepted as a
    # "narrow residual". The team-group guard gave that same None a SECOND
    # meaning: a guard working correctly. The residual stopped being narrow, and
    # every refused edit of a team group left a row saying an administrator
    # disabled a policy they never touched.
    #
    # For a table whose rule is "no record → no mutation", that is the inverted
    # error, and the worse one: a missing record says something is absent, a
    # false record ACCUSES someone.
    #
    # The model keeps its own guard — SCIM and OAuth never reach this handler —
    # so this is not the protection. It is what stops the protection from
    # writing history.
    if existing is not None:
        blocked = team_group_derived_changes(
            existing, form_data.model_dump(exclude_none=True, exclude={'reason'})
        )
        # Cheap half first: the classifier costs a query, and a form that changes
        # nothing derived cannot be refused whatever kind of group this is.
        if blocked and await team_group_kind(id, db=db) is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(
                    'This group belongs to a team. Its name and its masking policy follow '
                    'the team, so they cannot be changed here.'
                ),
            )

    event_type = None
    if existing is not None and form_data.permissions is not None:
        # `permissions=None` is not "clear the permissions": update_group_by_id
        # drops None fields, so nothing would change — auditing it would record
        # a mutation that never happened.
        was_enforced = group_enforces_pii_masking(existing.permissions)
        will_be_enforced = group_enforces_pii_masking(form_data.permissions)
        if was_enforced != will_be_enforced:
            # Only a real transition is recorded. Saving the group with some
            # other permission changed, or re-saving the same value, leaves no
            # PII row — a log of transitions that reports non-transitions is
            # noise in the one table that exists to be trusted.
            event_type = EVENT_POLICY_ENABLED if will_be_enforced else EVENT_POLICY_DISABLED

    if event_type == EVENT_POLICY_DISABLED and not (form_data.reason or '').strip():
        # Enforced on the route, not just in the UI: frontend validation is a
        # convenience, not a control. Turning protection off exposes people, so
        # it must say why.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('A reason is required to stop enforcing PII masking.'),
        )

    if event_type is not None:
        # BLOCKING, and BEFORE the mutation. If this write fails the policy must
        # stay as it was, and `Groups.update_group_by_id` commits on its own
        # session, so there is no shared transaction to roll back afterwards —
        # the only ordering that keeps "no record → no mutation" true is this one.
        #
        # ⚠️ The opposite of the PII detection audit trail, where events are
        # best-effort because they must never block a chat. Do not align the two.
        try:
            await PiiPolicyAudits.insert_event(
                event_type=event_type,
                group_id=id,
                actor_user_id=user.id,
                actor_email=user.email,
                reason=form_data.reason,
                db=db,
            )
        except Exception as e:
            log.exception(f'Refusing PII policy change on group {id}: audit write failed: {e}')
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_MESSAGES.DEFAULT(
                    'The change was not applied because it could not be recorded in the audit log.'
                ),
            )

    try:
        # `reason` is not a column — strip it before the model turns the form
        # into an UPDATE statement. update_group_by_id itself is unchanged.
        group = await Groups.update_group_by_id(id, GroupUpdateForm(**form_data.model_dump(exclude={'reason'})), db=db)
        if group is None and event_type is not None:
            # Still a residual, and now genuinely narrow again: every REFUSAL is
            # taken above, before anything is written, so what is left here is a
            # database failure. Kept, and still logged loudly, because the
            # alternative — auditing after the fact — is a policy change with no
            # record at all.
            log.error(
                f'PII policy audit recorded {event_type} for group {id} but the update failed; '
                f'the audit log now claims a change that did not happen.'
            )
        if group:
            return GroupResponse(
                **group.model_dump(),
                member_count=await Groups.get_group_member_count_by_id(group.id, db=db),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error updating group'),
            )
    except Exception as e:
        log.exception(f'Error updating group {id}: {e}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# GetPiiPolicyAuditByGroupId
############################

# Most groups will have a handful of events; a long-lived policy group can have
# hundreds. The panel takes the newest slice and is told the total, so it can say
# what it left out — silent truncation is banned here for the same reason as in
# the directory loader: a compliance list that shows part of itself without
# saying so asserts something untrue.
PII_AUDIT_PAGE_LIMIT = 200


class PiiPolicyAuditEventResponse(PiiPolicyAuditModel):
    """One event, plus the target's email resolved at read time.

    ⚠️ Asymmetric with `actor_email` on purpose, and worth knowing: the actor's
    email is DENORMALISED into the row at write time, so it survives the account
    being deleted. The target's is not — the schema stores only `user_id` — so it
    is looked up now and falls back to the id when the account is gone. Closing
    that gap means a column, and the schema is frozen for this release.
    """

    user_email: Optional[str] = None


class PiiPolicyAuditResponse(BaseModel):
    items: list[PiiPolicyAuditEventResponse] = []
    # Total for the group, not the length of `items`: their difference is exactly
    # what the panel must disclose.
    total: int = 0


def _may_read_pii_audit(user, group_id: str, db: AsyncSession) -> bool:
    """Who may read one group's policy audit.

    Admin-only today. A later phase adds the team leader (`team_members.role == 'owner'`
    for the team this group belongs to) as a SECOND condition here — deliberately
    one function rather than a second route, so widening the audience is an `or`
    on this line and not a rewrite of the guard. Blocked on the team-leader role
    not existing yet.
    """
    return user.role == 'admin'


@router.get('/id/{id}/pii-audit', response_model=PiiPolicyAuditResponse)
async def get_pii_policy_audit_by_group_id(
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if not _may_read_pii_audit(user, id, db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    events = await PiiPolicyAudits.get_events_by_group_id(id, limit=PII_AUDIT_PAGE_LIMIT, newest_first=True, db=db)

    # One lookup for every target named on the page, not one per row.
    target_ids = [event.user_id for event in events if event.user_id]
    emails = {u.id: u.email for u in await Users.get_users_by_user_ids(target_ids, db=db)} if target_ids else {}

    return PiiPolicyAuditResponse(
        items=[
            PiiPolicyAuditEventResponse(
                **event.model_dump(),
                user_email=emails.get(event.user_id) if event.user_id else None,
            )
            for event in events
        ],
        total=await PiiPolicyAudits.count_events_by_group_id(id, db=db),
    )


############################
# AddUserToGroupByUserIdAndGroupId
############################


async def _audit_membership_change(
    group_id: str,
    event_type: str,
    changing_user_ids: list,
    actor,
    reason: Optional[str],
    db: AsyncSession,
) -> None:
    """Record a membership change of a POLICY group. Raises to block the change.

    ⚠️ Only groups that carry `chat.pii_masking_enforced` are audited here.
    Membership of an ordinary group has nothing to do with PII, and a table that
    logged every group change would stop being readable as a PII record.

    Same ordering and the same blocking contract as the policy events: written
    before the mutation, exceptions propagate. `changing_user_ids` must already be
    filtered to those whose membership actually changes — this table records
    transitions, not requests.
    """
    if not changing_user_ids:
        return

    group = await Groups.get_group_by_id(group_id, db=db)
    if group is None or not group_enforces_pii_masking(group.permissions):
        return

    if event_type == EVENT_MEMBER_REMOVED and not (reason or '').strip():
        # Same rule as turning the policy off: taking someone out from under
        # protection exposes them, so it must say why. On the route, not
        # only in the UI — every caller of this endpoint is bound by it.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(
                'A reason is required to remove a user from a group that enforces PII masking.'
            ),
        )

    try:
        for user_id in changing_user_ids:
            await PiiPolicyAudits.insert_event(
                event_type=event_type,
                group_id=group_id,
                user_id=user_id,
                actor_user_id=actor.id,
                actor_email=actor.email,
                reason=reason,
                db=db,
            )
    except HTTPException:
        raise
    except Exception as e:
        log.exception(f'Refusing membership change on policy group {group_id}: audit write failed: {e}')
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT(
                'The change was not applied because it could not be recorded in the audit log.'
            ),
        )


@router.post('/id/{id}/users/add', response_model=Optional[GroupResponse])
async def add_user_to_group(
    id: str,
    form_data: GroupMembershipForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Add people to a group. Admin-wide, or a team owner within their own team.

    ⚠️ `get_verified_user`, not `get_admin_user`. The admin-only rule did not
    disappear — it moved into `authorise_policy_membership_change`, which is the
    first thing this function does. Swapping the dependency is the only change in
    level C that can fail OPEN, so the guard is a named call on the first line
    rather than a condition folded into something else.
    """
    if not form_data.user_ids:
        # ⚠️ Before the guard and before the audit: no query, no audit row, no
        # authorisation decision. A request that names nobody changes nothing, so
        # there is nothing to authorise and nothing to record — and asking the
        # guard anyway would make an empty body an authorisation event.
        return None

    await authorise_policy_membership_change(user, id, form_data.user_ids, db=db)

    try:
        if form_data.user_ids:
            form_data.user_ids = await Users.get_valid_user_ids(form_data.user_ids, db=db)
    except Exception as e:
        # Kept in its own try so this call keeps returning 400 exactly as it did
        # before the audit was inserted above it.
        log.exception(f'Error adding users to group {id}: {e}')
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT(e))

    # Only those who are not members yet are a change. `add_users_to_group`
    # already ignores duplicates, so auditing the request rather than the
    # transition would log memberships that already existed.
    already = set(await Groups.get_group_user_ids_by_id(id, db=db))
    await _audit_membership_change(
        id,
        EVENT_MEMBER_ADDED,
        [uid for uid in (form_data.user_ids or []) if uid not in already],
        user,
        form_data.reason,
        db,
    )

    try:
        group = await Groups.add_users_to_group(id, form_data.user_ids, db=db)
        if group:
            return GroupResponse(
                **group.model_dump(),
                member_count=await Groups.get_group_member_count_by_id(group.id, db=db),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error adding users to group'),
            )
    except Exception as e:
        log.exception(f'Error adding users to group {id}: {e}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.post('/id/{id}/users/remove', response_model=Optional[GroupResponse])
async def remove_users_from_group(
    id: str,
    form_data: GroupMembershipForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Take people out of a group. Same audience, same guard, same ordering.

    ⚠️ Guarded separately from `/users/add`, and tested separately. One guard on
    one of the two routes looks exactly like a guard on both — until somebody
    calls the other one.
    """
    if not form_data.user_ids:
        # Before the guard and before the audit. See `add_user_to_group`.
        return None

    await authorise_policy_membership_change(user, id, form_data.user_ids, db=db)

    # Only actual members are a change; asking to remove a non-member removes
    # nothing, and must not leave a record saying otherwise.
    members = set(await Groups.get_group_user_ids_by_id(id, db=db))
    await _audit_membership_change(
        id,
        EVENT_MEMBER_REMOVED,
        [uid for uid in (form_data.user_ids or []) if uid in members],
        user,
        form_data.reason,
        db,
    )

    try:
        # The reason is no longer only an audit field: the model refuses a removal
        # from an enforcing group without one. Passing it here keeps the route's own
        # 400 above as the readable error, and the model check as the backstop for
        # OAuth and SCIM, which never reach this handler.
        group = await Groups.remove_users_from_group(id, form_data.user_ids, reason=form_data.reason, db=db)
        if group:
            return GroupResponse(
                **group.model_dump(),
                member_count=await Groups.get_group_member_count_by_id(group.id, db=db),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error removing users from group'),
            )
    except Exception as e:
        log.exception(f'Error removing users from group {id}: {e}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# DeleteGroupById
############################


@router.delete('/id/{id}/delete', response_model=bool)
async def delete_group_by_id(id: str, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    try:
        result = await Groups.delete_group_by_id(id, db=db)
        if result:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT('Error deleting group'),
            )
    except Exception as e:
        log.exception(f'Error deleting group {id}: {e}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# PreviewGroupAccess
############################


@router.get('/id/{id}/preview')
async def preview_group_access(
    id: str,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Show what resources a group can access (preview audit)."""
    group = await Groups.get_group_by_id(id, db=db)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    group_ids = {group.id}

    # Batch-check accessible resources using existing AccessGrants
    all_models = await Models.get_all_models(db=db)
    accessible_model_ids = await AccessGrants.get_accessible_resource_ids(
        user_id='',
        resource_type='model',
        resource_ids=[m.id for m in all_models],
        permission='read',
        user_group_ids=group_ids,
        db=db,
    )

    all_knowledge = await Knowledges.get_knowledge_bases(db=db)
    accessible_knowledge_ids = await AccessGrants.get_accessible_resource_ids(
        user_id='',
        resource_type='knowledge',
        resource_ids=[k.id for k in all_knowledge],
        permission='read',
        user_group_ids=group_ids,
        db=db,
    )

    all_tools = await Tools.get_tools(defer_content=True, db=db)
    accessible_tool_ids = await AccessGrants.get_accessible_resource_ids(
        user_id='',
        resource_type='tool',
        resource_ids=[t.id for t in all_tools],
        permission='read',
        user_group_ids=group_ids,
        db=db,
    )

    active_models = [m for m in all_models if m.is_active]

    return {
        'group': {'id': group.id, 'name': group.name},
        'models': {
            'items': [{'id': m.id, 'name': m.name} for m in active_models if m.id in accessible_model_ids],
            'total': len(active_models),
        },
        'knowledge': {
            'items': [{'id': k.id, 'name': k.name} for k in all_knowledge if k.id in accessible_knowledge_ids],
            'total': len(all_knowledge),
        },
        'tools': {
            'items': [{'id': t.id, 'name': t.name} for t in all_tools if t.id in accessible_tool_ids],
            'total': len(all_tools),
        },
        'permissions': group.permissions or {},
    }
