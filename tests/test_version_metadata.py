from pathlib import Path

from lantern import cli


def test_pyproject_version_is_sourced_from_version_file():
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    version = (root / "VERSION").read_text(encoding="utf-8").strip()

    assert 'dynamic = ["version"]' in pyproject
    assert 'version = {file = ["VERSION"]}' in pyproject
    assert f'version = "{version}"' not in pyproject


def test_application_version_prefers_installed_distribution_metadata(monkeypatch):
    monkeypatch.setattr(cli.importlib_metadata, "version", lambda package: "2.4.6")

    assert cli._application_version() == "2.4.6"


def test_application_version_falls_back_to_version_file(monkeypatch, tmp_path):
    version_file = tmp_path / "VERSION"
    version_file.write_text("3.5.7\n", encoding="utf-8")

    def missing_distribution(_package):
        raise cli.importlib_metadata.PackageNotFoundError("git-lantern")

    monkeypatch.setattr(cli.importlib_metadata, "version", missing_distribution)
    monkeypatch.setattr(cli, "_VERSION_FILE", str(version_file))

    assert cli._application_version() == "3.5.7"
