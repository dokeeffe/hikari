import pytest

from hikari.config import HikariSettings, load_settings, write_default_config


def test_load_settings_reads_current_directory_hikari_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hikari.toml").write_text(
        'obs_code = "ODEA"\n'
        "\n"
        "[photometry]\n"
        "aperture_radius = 5.5\n"
        "\n"
        "[web]\n"
        'host = "0.0.0.0"\n'
    )
    (tmp_path / "hikari.yaml").write_text('obs_code: "YAML"\n')
    monkeypatch.setenv("HIKARI_OBS_CODE", "ENV")
    monkeypatch.setenv("HIKARI_APERTURE_RADIUS", "2")

    settings = load_settings()

    assert settings.obs_code == "ODEA"
    assert settings.aperture_radius == 5.5
    assert settings.web_host == "0.0.0.0"
    assert settings.web_port == HikariSettings().web_port


def test_load_settings_requires_current_directory_hikari_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hikari.yaml").write_text('obs_code: "YAML"\n')

    with pytest.raises(FileNotFoundError, match="hikari config"):
        load_settings()


def test_write_default_config_creates_current_directory_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    write_default_config()

    assert (tmp_path / "hikari.toml").is_file()
    settings = load_settings()
    assert settings.aperture_radius == HikariSettings().aperture_radius
    assert settings.web_port == HikariSettings().web_port
