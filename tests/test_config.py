import pytest

from hikari.config import HikariSettings, load_settings, save_settings, write_default_config


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


def test_save_settings_round_trips_all_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original = HikariSettings(
        obs_code='AAVSO "observer"',
        observer_notes="scope\nnotes",
        aperture_radius=5.5,
        sky_inner_radius=7.0,
        sky_outer_radius=11.0,
        fwhm=2.2,
        source_snr=3.0,
        ensemble_size=7,
        linear_limit=55000.0,
        brightest_comp_star=8.0,
        dimmest_comp_star=16.0,
        field_of_view=21.0,
        excluded_auids=["123", "AUID with spaces"],
        output_dir=tmp_path / "output dir",
        database_url="sqlite:///test database.db",
        web_host="0.0.0.0",
        web_port=9000,
        cache_dir=tmp_path / "cache",
    )

    saved_path = save_settings(original)

    assert saved_path == tmp_path / "hikari.toml"
    assert "# pixels" in saved_path.read_text()
    assert load_settings() == original
