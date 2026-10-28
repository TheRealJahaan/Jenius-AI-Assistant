from datetime import timedelta

from helpers import signup
from sqlalchemy import select

from jenius.db import as_utc, utcnow
from jenius.models import User


def make_admin(database, email):
    with database.sessions() as db:
        db.scalar(select(User).where(User.email == email)).is_admin = True
        db.commit()


def test_paying_needs_an_admin_to_confirm(make_client, database):
    customer, admin = make_client(), make_client()
    signup(customer, "cust@example.com", "Cust")
    signup(admin, "owner@example.com", "Owner")
    make_admin(database, "owner@example.com")

    claim = customer.post("/api/billing/upi-claims", json={"cycle": "annual", "upi_reference": "412345678901"})
    assert claim.status_code == 201 and claim.json()["amount_inr"] == 6708 and claim.json()["status"] == "pending"
    assert customer.get("/api/auth/me").json()["plan"] == "free"  # nothing upgrades on the browser's word
    dup = customer.post("/api/billing/upi-claims", json={"cycle": "monthly", "upi_reference": "412345678901"})
    assert dup.status_code == 409

    assert customer.get("/api/admin/payments").status_code == 403
    pending = admin.get("/api/admin/payments").json()
    assert [p["user_email"] for p in pending] == ["cust@example.com"]
    assert admin.post(f"/api/admin/payments/{pending[0]['id']}/approve", json={}).status_code == 200
    assert admin.post(f"/api/admin/payments/{pending[0]['id']}/approve", json={}).status_code == 409

    me = customer.get("/api/auth/me").json()
    assert me["plan"] == "pro" and me["limits"]["daily_messages"] == 300
    with database.sessions() as db:
        expires = as_utc(db.scalar(select(User).where(User.email == "cust@example.com")).plan_expires_at)
        assert timedelta(days=365) < expires - utcnow() < timedelta(days=367)


def test_rejected_payment_keeps_the_free_plan(make_client, database):
    customer, admin = make_client(), make_client()
    signup(customer, "cust@example.com", "Cust")
    signup(admin, "owner@example.com", "Owner")
    make_admin(database, "owner@example.com")
    claim = customer.post("/api/billing/upi-claims", json={"cycle": "monthly", "upi_reference": "UTR99887766"}).json()
    admin.post(f"/api/admin/payments/{claim['id']}/reject", json={"note": "Not received"})
    billing = customer.get("/api/billing").json()
    assert billing["plan"] == "free" and billing["payments"][0]["status"] == "rejected"
    assert billing["payments"][0]["note"] == "Not received"


def test_upi_reference_is_validated(client):
    signup(client)
    for ref in ("short", "has spaces 123", "<script>alert(1)</script>"):
        assert client.post("/api/billing/upi-claims", json={"cycle": "monthly", "upi_reference": ref}).status_code == 422


def test_upi_qr_is_generated_from_settings(make_client):
    off = make_client()
    assert off.get("/api/billing/upi-qr.svg").status_code == 404
    assert off.get("/api/config").json()["upi_links"] is None
    on = make_client(upi_id="jenius@okaxis", pro_monthly_inr=799)
    svg = on.get("/api/billing/upi-qr.svg?cycle=monthly")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml") and b"<svg" in svg.content
    link = on.get("/api/config").json()["upi_links"]["monthly"]
    assert link.startswith("upi://pay?pa=jenius%40okaxis") and "am=799.00" in link and "cu=INR" in link
