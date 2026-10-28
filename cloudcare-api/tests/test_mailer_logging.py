"""Password-reset links must never be logged outside development."""
import logging

from app import mailer


def _call(caplog, env, monkeypatch):
    monkeypatch.setattr(mailer.settings, "env", env)
    monkeypatch.setattr(mailer.settings, "smtp_host", None)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="cloudcare.mailer"):
        result = mailer.send_email(
            "patient@example.com",
            "Reset your CloudCare password",
            "Open this link: https://cloudcare.test/reset?token=live-secret",
        )
    return result, caplog.text


def test_development_logs_the_message_body(monkeypatch, caplog):
    result, logs = _call(caplog, "development", monkeypatch)
    assert result is True
    assert "live-secret" in logs


def test_staging_and_production_withhold_the_link(monkeypatch, caplog):
    for env in ("staging", "production"):
        result, logs = _call(caplog, env, monkeypatch)
        assert result is False
        assert "live-secret" not in logs
        assert "not sent" in logs
