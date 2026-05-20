import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class _DummyDbSession:
    def get(self, *_args, **_kwargs):
        return None

    def close(self):
        return None


@pytest.fixture
def test_app(monkeypatch):
    import app.run as run_module
    from app.auth.login import deps as auth_deps
    from app.db import get_db

    startup_db = _DummyDbSession()
    request_db = _DummyDbSession()

    monkeypatch.setattr(run_module, "ensure_assessment_types_seeded", lambda _db: None)
    monkeypatch.setattr(run_module, "SessionLocal", lambda: startup_db)

    app = run_module.get_app()

    def override_get_db():
        yield request_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[auth_deps.get_current_user_id] = lambda: 1
    app.dependency_overrides[auth_deps.require_authenticated_user] = (
        lambda: SimpleNamespace(id=1, is_verified=True)
    )

    yield app

    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    with TestClient(test_app) as test_client:
        yield test_client
