from app.auth.signup.router import router as signup_router
from app.auth.signup.verify_router import router as verify_router
from app.auth.urls import (
    build_frontend_verification_url,
    build_frontend_verification_url_template,
)


def test_frontend_verification_url_uses_frontend_url_env(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://frontend.example")

    assert build_frontend_verification_url("abc123") == "https://frontend.example/verify/abc123"
    assert build_frontend_verification_url_template() == "https://frontend.example/verify/{token}"


def test_public_verify_route_is_not_nested_under_auth_prefix():
    assert any(route.path == "/verify/{token}" for route in verify_router.routes)
    assert all(route.path != "/verify/{token}" for route in signup_router.routes)
