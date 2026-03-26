from pathlib import Path


def test_pyproject_version_is_sourced_from_version_file():
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    version = (root / "VERSION").read_text(encoding="utf-8").strip()

    assert 'dynamic = ["version"]' in pyproject
    assert 'version = {file = ["VERSION"]}' in pyproject
    assert f'version = "{version}"' not in pyproject
