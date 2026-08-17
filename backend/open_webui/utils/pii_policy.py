"""Helpers for the team PII masking policy.

Deliberately NOT in `utils/access_control/` — that module is upstream
(`tim@openwebui.com`), and every line we add there is a line the next upstream
merge has to reconcile. Only the one extraction that could not be avoided lives
there; everything policy-specific lives here, in our own file.
"""

from typing import Any, Optional

from open_webui.config import PII_MASKING_ENFORCED_PERMISSION


def group_enforces_pii_masking(permissions: Optional[dict]) -> bool:
    """Whether ONE group's own permissions carry the policy.

    ⚠️ Not a substitute for `has_permission_for_groups`, and not its per-group
    inner loop: this deliberately does NOT fall back to the instance defaults.
    The question it answers is "does this group say yes", which is what
    membership actions and the audit need — a group inherits nothing here.

    "Is this user enforced" is the other question, and it keeps its single
    implementation in `has_permission_for_groups`.

    A missing key is False, not unknown. Fail-closed governs
    exceptions while resolving a user's effective policy, never a plain negative
    answer about a group's stored permissions.
    """
    node: Any = permissions or {}
    for part in PII_MASKING_ENFORCED_PERMISSION.split('.'):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return bool(node)
