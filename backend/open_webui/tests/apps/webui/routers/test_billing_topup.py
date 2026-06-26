from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from open_webui.main import app
from open_webui.utils.auth import get_verified_user


client = TestClient(app)


def _user(**overrides):
    base = {
        "id": "u1",
        "email": "user@example.com",
        "role": "user",
        "name": "Test User",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _billing(**overrides):
    base = {
        "id": "b1",
        "user_id": "u1",
        "plan_tier": "paid",
        "subscription_status": "active",
        "stripe_customer_id": "cus_123",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _team(**overrides):
    base = {
        "id": "t1",
        "owner_id": "u1",
        "stripe_customer_id": "cus_123",
        "subscription_status": "active",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _pack(**overrides):
    base = {"id": "pack_basic", "credits": 1000, "price_eur": 5}
    base.update(overrides)
    return SimpleNamespace(**base)


def override_user(user_obj):
    app.dependency_overrides[get_verified_user] = lambda: user_obj


def clear_overrides():
    app.dependency_overrides.clear()


class TestCreateTopup:
    def test_topup_requires_paid_or_team_plan(self):
        override_user(_user())
        with patch("open_webui.routers.billing.StripeBillings.get_by_user_id", return_value=_billing(plan_tier="free")):
            r = client.post("/api/v1/billing/topup", json={"top_up_id": "pack_basic"})
        assert r.status_code == 402
        clear_overrides()

    def test_topup_requires_active_subscription(self):
        override_user(_user())
        with patch("open_webui.routers.billing.StripeBillings.get_by_user_id", return_value=_billing(subscription_status="canceled")):
            r = client.post("/api/v1/billing/topup", json={"top_up_id": "pack_basic"})
        assert r.status_code == 402
        clear_overrides()

    def test_topup_invalid_pack(self):
        override_user(_user())
        with patch("open_webui.routers.billing.TopupPacks", create=True) as TopupPacks:
            TopupPacks.get_by_id.return_value = None
            with patch("open_webui.routers.billing.StripeBillings.get_by_user_id", return_value=_billing()):
                r = client.post("/api/v1/billing/topup", json={"top_up_id": "nope"})
        assert r.status_code == 400
        clear_overrides()

    def test_topup_missing_billing_account(self):
        override_user(_user())
        with patch("open_webui.routers.billing.TopupPacks", create=True) as TopupPacks:
            TopupPacks.get_by_id.return_value = _pack()
            with patch("open_webui.routers.billing.StripeBillings.get_by_user_id", return_value=_billing(stripe_customer_id=None)):
                r = client.post("/api/v1/billing/topup", json={"top_up_id": "pack_basic"})
        assert r.status_code == 400
        clear_overrides()

    def test_topup_team_missing_customer(self):
        override_user(_user())
        with patch("open_webui.routers.billing.TopupPacks", create=True) as TopupPacks:
            TopupPacks.get_by_id.return_value = _pack()
            with patch("open_webui.routers.billing.StripeBillings.get_by_user_id", return_value=_billing(plan_tier="team")):
                with patch("open_webui.routers.billing.Teams.get_by_owner_user_id", return_value=_team(stripe_customer_id=None)):
                    r = client.post("/api/v1/billing/topup", json={"top_up_id": "pack_basic"})
        assert r.status_code == 400
        clear_overrides()

    def test_webhook_topup_idempotent(self):
        event = SimpleNamespace(
            type="checkout.session.completed",
            data=SimpleNamespace(
                object=SimpleNamespace(
                    id="cs_123",
                    payment_status="paid",
                    metadata={"type": "topup", "user_id": "u1", "credits": "500"},
                )
            ),
        )
        with patch("open_webui.routers.billing.stripe.Webhook.construct_event", return_value=event):
            with patch("open_webui.routers.billing.StripeBillings.get_by_topup_checkout_session_id", return_value=True):
                r = client.post("/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "sig"})
        assert r.status_code == 200

    def test_webhook_topup_skipped_if_not_paid(self):
        event = SimpleNamespace(
            type="checkout.session.completed",
            data=SimpleNamespace(
                object=SimpleNamespace(
                    id="cs_123",
                    payment_status="unpaid",
                    metadata={"type": "topup", "user_id": "u1", "credits": "500"},
                )
            ),
        )
        with patch("open_webui.routers.billing.stripe.Webhook.construct_event", return_value=event):
            with patch("open_webui.routers.billing.StripeBillings.add_top_up_credits") as mock_add:
                r = client.post("/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "sig"})
        assert r.status_code == 200
        mock_add.assert_not_called()
