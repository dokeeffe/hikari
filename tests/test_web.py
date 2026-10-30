import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from hikari.config import HikariSettings, load_settings
from hikari.web import app as web_app


def test_configured_obs_code_comes_from_config_file(monkeypatch):
    monkeypatch.setattr(web_app, "load_settings", lambda: HikariSettings(obs_code="ODEA"))

    assert web_app._configured_obs_code() == "ODEA"


def test_settings_api_reads_and_saves_current_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    web_app.save_settings(HikariSettings(obs_code="OLD"))

    current = web_app.api_get_settings()
    assert current["config_path"] == str(tmp_path / "hikari.toml")
    assert current["settings"]["obs_code"] == "OLD"

    updated = web_app.api_save_settings(HikariSettings(obs_code="NEW"))

    assert updated["status"] == "saved"
    assert updated["settings"]["obs_code"] == "NEW"
    assert load_settings().obs_code == "NEW"


def test_job_csv_uses_configured_obs_code(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "get_job",
        lambda db, job_id: SimpleNamespace(chart_id="chart", obs_code="OLD"),
    )
    monkeypatch.setattr(web_app, "list_observations", lambda db, job_id: [])
    monkeypatch.setattr(web_app, "load_settings", lambda: HikariSettings(obs_code="ODEA"))

    csv = web_app.api_aavso_csv(1, db=None)

    assert "#OBSCODE=ODEA" in csv


def test_run_csv_uses_configured_obs_code(monkeypatch):
    monkeypatch.setattr(web_app, "get_run", lambda db, run_id: SimpleNamespace(jobs=[]))
    monkeypatch.setattr(web_app, "list_observations_for_run", lambda db, run_id: [])
    monkeypatch.setattr(web_app, "load_settings", lambda: HikariSettings(obs_code="ODEA"))

    csv = web_app.api_run_aavso_csv(1, db=None)

    assert "#OBSCODE=ODEA" in csv


def test_run_job_summary_separates_accepted_rejected_and_failed(monkeypatch):
    now = datetime.datetime.now()
    observations = [
        SimpleNamespace(status="success", rejected=False),
        SimpleNamespace(status="success", rejected=True),
        SimpleNamespace(status="failed", rejected=False),
    ]
    job = SimpleNamespace(
        id=1,
        object_name="Target",
        filter="V",
        status="complete",
        created_at=now,
        completed_at=now,
        observations=observations,
        chart_id="chart",
        aavso_csv_path=None,
    )
    monkeypatch.setattr(
        web_app,
        "get_run",
        lambda db, run_id: SimpleNamespace(jobs=[job]),
    )

    summary = web_app.api_run_jobs(1, db=None)[0]

    assert summary.n_observations == 1
    assert summary.n_rejected == 1
    assert summary.n_failed == 1


@pytest.mark.parametrize("obs_code", [None, "", "   "])
def test_configured_obs_code_requires_non_empty_value(monkeypatch, obs_code):
    monkeypatch.setattr(
        web_app,
        "load_settings",
        lambda: SimpleNamespace(obs_code=obs_code),
    )

    with pytest.raises(HTTPException) as exc_info:
        web_app._configured_obs_code()

    assert exc_info.value.status_code == 400
    assert "obs_code in hikari.toml" in exc_info.value.detail
