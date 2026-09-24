from fastapi.testclient import TestClient


def test_health_reports_wal_db(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "journal_mode": "wal"}
