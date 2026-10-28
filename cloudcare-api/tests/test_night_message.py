"""Late-evening starts get the right message at the 21:30 boundary."""
from app.scheduling import LAST_START_MESSAGE, NIGHT_BLOCK_MESSAGE
from tests.test_booking import _book, _future_day, _patient_token


def test_2145_and_2200_explain_different_rules(client, seeded_ids):
    token = _patient_token(client, "late.message@example.com")
    day = _future_day()

    late_evening = _book(client, token, seeded_ids["doctor_id"], day, 21, 45)
    assert late_evening.status_code == 400, late_evening.text
    assert late_evening.json()["detail"] == LAST_START_MESSAGE
    assert "9:30 PM" in late_evening.json()["detail"]

    at_ten = _book(client, token, seeded_ids["doctor_id"], day, 22, 0)
    assert at_ten.status_code == 400
    assert at_ten.json()["detail"] == NIGHT_BLOCK_MESSAGE

    just_after_last_start = _book(
        client, token, seeded_ids["doctor_id"], day, 21, 31
    )
    assert just_after_last_start.status_code == 400
    assert just_after_last_start.json()["detail"] == LAST_START_MESSAGE


def test_early_morning_keeps_the_night_message(client, seeded_ids):
    token = _patient_token(client, "early.message@example.com")
    day = _future_day()

    early = _book(client, token, seeded_ids["doctor_id"], day, 4, 59)
    assert early.status_code == 400
    assert early.json()["detail"] == NIGHT_BLOCK_MESSAGE
