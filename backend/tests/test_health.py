from pathlib import Path

from fastapi.testclient import TestClient

from argos.config import Settings
from argos.main import create_app


def test_health_reports_wal_db(tmp_path: Path) -> None:
    app = create_app(Settings(db_path=tmp_path / "test.db"))
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "journal_mode": "wal"}
