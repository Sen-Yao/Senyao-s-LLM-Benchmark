from fastapi.testclient import TestClient

from backend.app.main import app


def test_management_crud_and_incremental_queue():
    with TestClient(app) as client:
        provider_payload = {
            "name": "local-test-provider",
            "api_base": "http://127.0.0.1:9999/v1",
            "api_key": "test-secret",
        }
        provider = client.post("/api/providers", json=provider_payload).json()
        assert provider["name"] == "local-test-provider"
        assert provider["api_key_saved"] is True
        assert provider["api_key_fingerprint"]

        patched = client.patch(
            f"/api/providers/{provider['id']}",
            json={"notes": "patched", "enabled": False},
        ).json()
        assert patched["notes"] == "patched"
        assert patched["enabled"] is False

        model = client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "display_name": "Local Test Model",
                "model_id": "local-test-model",
            },
        ).json()
        assert model["provider_name"] == "local-test-provider"

        model = client.patch(
            f"/api/models/{model['id']}",
            json={"display_name": "Local Test Model v2", "enabled": False},
        ).json()
        assert model["display_name"] == "Local Test Model v2"
        assert model["enabled"] is False

        judge = client.post(
            "/api/judges",
            json={
                "provider_id": provider["id"],
                "name": "Local Judge",
                "model_id": "local-judge-model",
                "temperature": 0,
            },
        ).json()
        assert judge["provider_name"] == "local-test-provider"

        judge = client.patch(f"/api/judges/{judge['id']}", json={"temperature": 0.2}).json()
        assert judge["temperature"] == 0.2

        queued = client.post(
            "/api/runs/incremental/task/example-task",
            json={"model_ids": [model["id"]], "judge_profile_id": judge["id"]},
        ).json()
        assert queued == {"status": "queued", "task_slug": "example-task", "model_ids": [model["id"]]}

        assert client.delete(f"/api/judges/{judge['id']}").json() == {"ok": True}
        assert client.delete(f"/api/models/{model['id']}").json() == {"ok": True}
        assert client.delete(f"/api/providers/{provider['id']}").json() == {"ok": True}
