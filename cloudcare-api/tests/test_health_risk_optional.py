"""The health-risk agent is optional: CloudCare must start without it and
serve a clear 503 on every /api/health-risk route."""
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

API_DIR = Path(__file__).resolve().parents[1]


def test_app_imports_when_agent_folder_is_absent():
    """A fresh interpreter must import app.main with no agent available."""
    env = {
        **os.environ,
        "ENV": "development",
        "USE_SQLITE": "true",
        "RATE_LIMIT_LOGIN": "",
        "RATE_LIMIT_REGISTER": "",
        "HEALTH_RISK_AGENT_DIR": str(API_DIR / "definitely-not-here"),
    }
    result = subprocess.run(
        [sys.executable, "-c", "import app.main; print('imported')"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_health_risk_routes_return_503_when_agent_is_absent():
    client = TestClient(app)
    for method, path, body in [
        ("get", "/api/health-risk/presets", None),
        ("get", "/api/health-risk/health", None),
        ("post", "/api/health-risk/predict", {"age": 40}),
        ("post", "/api/health-risk/predict/batch", []),
        ("post", "/api/health-risk/simulate", {"patient": {}, "interventions": {}}),
        ("post", "/api/health-risk/chat", {"patient": {}, "query": "hello"}),
        ("post", "/api/health-risk/trajectory", {"patient": {}}),
        ("post", "/api/health-risk/soap-note", {"age": 40}),
    ]:
        response = client.request(method.upper(), path, json=body)
        assert response.status_code == 503, (path, response.status_code, response.text)
        assert response.json() == {
            "detail": "The health risk assistant is not available right now."
        }
