"""Guard against reintroducing the domain-group auto-assignment removed from
routers/auths.py's signup() flow.

assign_user_to_domain_group() used to auto-add every new signup whose email
domain matched a hardcoded 11-domain allowlist into a hardcoded "Manchester
Roundtable" group with elevated workspace/sharing/chat/features
permissions. It was removed at the user's explicit request (not a
correctness bug — deliberate fork business logic that's no longer wanted).
This test asserts both the function and its call site stay gone, via
source inspection rather than a full HTTP-level signup() test (auths.py
has no working integration-test harness in this fork's own tests/ tree —
see apps/webui/routers/conftest.py's collect_ignore for the dead upstream
test file this predates).
"""

import inspect

from open_webui.routers import auths as auths_router


class TestDomainGroupAutoAssignmentRemoved:
    def test_assign_user_to_domain_group_function_is_gone(self):
        assert not hasattr(auths_router, "assign_user_to_domain_group"), (
            "assign_user_to_domain_group() was removed from routers/auths.py "
            "at the user's request — it reappeared."
        )

    def test_signup_no_longer_calls_it(self):
        source = inspect.getsource(auths_router.signup)
        assert "assign_user_to_domain_group" not in source, (
            "signup() still references assign_user_to_domain_group — "
            "the removed call site was reintroduced."
        )

    def test_manchester_roundtable_hardcoding_is_gone(self):
        """The hardcoded target group name shouldn't linger anywhere in the module —
        a partial revert (e.g. re-adding the call but not the function) would still
        leave this string behind."""
        source = inspect.getsource(auths_router)
        assert "Manchester Roundtable" not in source

    def test_group_update_form_import_not_reintroduced_unused(self):
        """GroupUpdateForm was only used by the removed function. If a future edit
        re-imports it, it should come with a real use — this isn't a strict
        guarantee, but catches the exact prior state (import present, zero uses)."""
        source = inspect.getsource(auths_router)
        if "GroupUpdateForm" in source:
            # If it's back, it must actually be used somewhere beyond the import line.
            uses = source.count("GroupUpdateForm")
            assert uses > 1, (
                "GroupUpdateForm is imported but appears unused — matches the exact "
                "dead-import state cleaned up when assign_user_to_domain_group was removed."
            )
