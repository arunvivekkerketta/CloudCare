"""The bundled health-risk model is usable by patients and saves their score."""
from tests.conftest import auth_headers, login, register_patient


MINIMAL_PATIENT = {
    "age": 58,
    "sex": 1,
    "bmi": 31.2,
    "systolic_bp": 148,
    "diastolic_bp": 92,
    "total_cholesterol": 240,
    "hdl_cholesterol": 38,
    "ldl_cholesterol": 165,
    "fasting_glucose": 132,
    "waist_circumference": 106,
}


def test_patient_can_run_a_health_risk_assessment(client):
    register_patient(client, "risk.patient@example.com", name="Risk Patient")
    login_response = login(
        client, "risk.patient@example.com", "Patient!Pass123", "patient"
    )
    assert login_response.status_code == 200, login_response.text
    headers = auth_headers(login_response.json()["access_token"])

    response = client.post(
        "/api/health-risk/predict", headers=headers, json=MINIMAL_PATIENT
    )
    assert response.status_code == 200, response.text
    report = response.json()

    assert report["saved_to_profile"] is True
    assert set(report["risks"]) == {
        "diabetes_risk",
        "heart_disease_risk",
        "kidney_disease_risk",
        "stroke_risk",
    }
    assert 0 <= report["composite_score"]["cardiometabolic_burden_score"] <= 100
    assert report["risks"]["heart_disease_risk"]["risk_percent"] > 0
    assert report["disclaimer"]

    profile = client.get("/api/patients/me", headers=headers)
    assert profile.status_code == 200, profile.text
    assert profile.json()["risk_score"] == round(
        report["composite_score"]["cardiometabolic_burden_score"]
    )


def test_health_risk_requires_a_signed_in_user(client):
    response = client.post("/api/health-risk/predict", json=MINIMAL_PATIENT)
    assert response.status_code in {401, 403}
