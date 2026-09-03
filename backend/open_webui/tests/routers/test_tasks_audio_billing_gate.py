"""Guard against regressing the tasks.py / audio.py billing-gate fixes.

Both files had background/support endpoints that trigger real, billable
LLM or TTS/STT provider calls but were gated only on get_verified_user,
not check_billing_access — a user with exhausted credits or a canceled
subscription could generate unlimited titles/tags/follow-ups/
autocompletions/emoji reactions/MOA responses (tasks.py, each calls
utils/chat.py's generate_chat_completion internally) or unlimited
speech/transcription audio (audio.py's speech/transcription, on a cache
miss) with zero billing enforcement. Same shape as the retrieval.py gap
fixed earlier — see test_retrieval_billing_gate.py for the sibling test
and the fuller rationale on why route-introspection (not a full request)
is the right level to test this at.
"""

from open_webui.routers import audio as audio_router
from open_webui.routers import tasks as tasks_router
from open_webui.routers.billing import check_billing_access
from open_webui.utils.auth import get_verified_user

TASKS_BILLED_PATHS = {
    "/title/completions",
    "/follow_up/completions",
    "/tags/completions",
    "/image_prompt/completions",
    "/queries/completions",
    "/auto/completions",
    "/emoji/completions",
    "/moa/completions",
}

# Config/utility endpoints — not embedding/LLM/TTS/STT calls, deliberately
# left on get_verified_user. Regression-guarded the other direction so
# nobody "fixes" these the same way without a deliberate decision.
TASKS_UNBILLED_PATHS = {
    "/active/chats",
    "/config",
}

AUDIO_BILLED_PATHS = {
    "/speech",
    "/transcriptions",
}

AUDIO_UNBILLED_PATHS = {
    "/models",
    "/voices",
}


def _user_dependency_callable(route):
    for dependency in route.dependant.dependencies:
        if dependency.name == "user":
            return dependency.call
    return None


def _routes_by_path(router):
    routes = {}
    for route in router.routes:
        routes.setdefault(route.path, []).append(route)
    return routes


class TestTasksBillingGate:
    def test_all_billed_paths_exist(self):
        routes = _routes_by_path(tasks_router.router)
        missing = TASKS_BILLED_PATHS - routes.keys()
        assert not missing, f"Expected tasks routes not found: {missing}"

    def test_billed_endpoints_use_check_billing_access(self):
        routes = _routes_by_path(tasks_router.router)
        for path in sorted(TASKS_BILLED_PATHS):
            for route in routes[path]:
                dep = _user_dependency_callable(route)
                assert dep is check_billing_access, (
                    f"{path} ({route.methods}) uses {dep!r} as its `user` dependency, "
                    f"expected check_billing_access — this endpoint generates a real, "
                    f"billable LLM completion and must be billing-gated."
                )

    def test_billed_endpoints_do_not_use_plain_get_verified_user(self):
        routes = _routes_by_path(tasks_router.router)
        for path in sorted(TASKS_BILLED_PATHS):
            for route in routes[path]:
                dep = _user_dependency_callable(route)
                assert dep is not get_verified_user, (
                    f"{path} ({route.methods}) regressed back to plain get_verified_user "
                    f"— billing bypass reintroduced."
                )

    def test_unbilled_endpoints_intentionally_left_alone(self):
        routes = _routes_by_path(tasks_router.router)
        for path in sorted(TASKS_UNBILLED_PATHS):
            for route in routes[path]:
                dep = _user_dependency_callable(route)
                assert dep is get_verified_user


class TestAudioBillingGate:
    def test_all_billed_paths_exist(self):
        routes = _routes_by_path(audio_router.router)
        missing = AUDIO_BILLED_PATHS - routes.keys()
        assert not missing, f"Expected audio routes not found: {missing}"

    def test_billed_endpoints_use_check_billing_access(self):
        routes = _routes_by_path(audio_router.router)
        for path in sorted(AUDIO_BILLED_PATHS):
            for route in routes[path]:
                dep = _user_dependency_callable(route)
                assert dep is check_billing_access, (
                    f"{path} ({route.methods}) uses {dep!r} as its `user` dependency, "
                    f"expected check_billing_access — this endpoint generates a real, "
                    f"billable TTS/STT provider call and must be billing-gated."
                )

    def test_billed_endpoints_do_not_use_plain_get_verified_user(self):
        routes = _routes_by_path(audio_router.router)
        for path in sorted(AUDIO_BILLED_PATHS):
            for route in routes[path]:
                dep = _user_dependency_callable(route)
                assert dep is not get_verified_user, (
                    f"{path} ({route.methods}) regressed back to plain get_verified_user "
                    f"— billing bypass reintroduced."
                )

    def test_unbilled_endpoints_intentionally_left_alone(self):
        routes = _routes_by_path(audio_router.router)
        for path in sorted(AUDIO_UNBILLED_PATHS):
            for route in routes[path]:
                dep = _user_dependency_callable(route)
                assert dep is get_verified_user
