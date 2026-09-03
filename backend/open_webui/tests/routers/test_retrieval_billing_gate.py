"""Guard against regressing the RAG/retrieval billing-gate fix.

process/file, process/text, process/web, query/doc, query/collection, and
process/files/batch each trigger a real, billable embedding-API call
(get_embedding_function -> retrieval/utils.py's generate_embeddings), but
were previously gated only on get_verified_user, not check_billing_access
— a user with exhausted credits or a canceled subscription could freely
upload/process documents and run RAG queries with zero billing
enforcement. This test asserts the fix (check_billing_access on all 6)
stays in place, by inspecting each route's actual FastAPI dependency graph
rather than re-testing check_billing_access's own logic (already covered
by test_billing.py).
"""

from open_webui.routers import retrieval as retrieval_router
from open_webui.routers.billing import check_billing_access
from open_webui.utils.auth import get_verified_user

BILLED_PATHS = {
    "/process/file",
    "/process/text",
    "/process/web",
    "/query/doc",
    "/query/collection",
    "/process/files/batch",
}


def _user_dependency_callable(route):
    """Return the callable behind this route's `user` dependency param, if any."""
    for dependency in route.dependant.dependencies:
        if dependency.name == "user":
            return dependency.call
    return None


def _routes_by_path():
    routes = {}
    for route in retrieval_router.router.routes:
        routes.setdefault(route.path, []).append(route)
    return routes


class TestRetrievalBillingGate:
    def test_all_billed_paths_exist(self):
        """Sanity check the path list itself hasn't drifted from the real router."""
        routes = _routes_by_path()
        missing = BILLED_PATHS - routes.keys()
        assert not missing, f"Expected retrieval routes not found: {missing}"

    def test_billed_endpoints_use_check_billing_access(self):
        routes = _routes_by_path()
        for path in sorted(BILLED_PATHS):
            for route in routes[path]:
                dep = _user_dependency_callable(route)
                assert dep is check_billing_access, (
                    f"{path} ({route.methods}) uses {dep!r} as its `user` dependency, "
                    f"expected check_billing_access — this endpoint generates a real, "
                    f"billable embedding-API call and must be billing-gated."
                )

    def test_billed_endpoints_do_not_use_plain_get_verified_user(self):
        """Explicit regression guard for the exact bug that was fixed."""
        routes = _routes_by_path()
        for path in sorted(BILLED_PATHS):
            for route in routes[path]:
                dep = _user_dependency_callable(route)
                assert dep is not get_verified_user, (
                    f"{path} ({route.methods}) regressed back to plain get_verified_user "
                    f"— billing bypass reintroduced."
                )

    def test_web_search_intentionally_left_on_get_verified_user(self):
        """process/web/search doesn't generate embeddings (web search, different cost
        profile) — confirm it wasn't swept up in the fix by accident, and that nobody
        "fixes" it the same way without a deliberate decision."""
        routes = _routes_by_path()
        for route in routes["/process/web/search"]:
            dep = _user_dependency_callable(route)
            assert dep is get_verified_user
