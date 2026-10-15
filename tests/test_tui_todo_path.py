"""The TUI resolves a relative TODO path against the repository it selected."""
import os

from lantern import cli


def test_relative_todo_path_is_resolved_against_the_selected_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "notes").mkdir(parents=True)
    (repo / "notes" / "TODO.md").write_text("## Item\nbody\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    answers = iter(["notes/TODO.md", None])  # TODO path, then cancel at the limit prompt
    errors = []
    monkeypatch.setattr(cli, "_validate_session_root", lambda *a: True)
    monkeypatch.setattr(cli, "find_repos", lambda *a: [str(repo)])
    monkeypatch.setattr(cli, "_dialog_menu", lambda *a, **k: "1")
    monkeypatch.setattr(cli, "_dialog_inputbox", lambda *a, **k: next(answers))
    monkeypatch.setattr(cli, "_dialog_msgbox", lambda title, text, *a, **k: errors.append(text))

    session = {"root": str(tmp_path), "max_depth": 2, "include_hidden": False}
    cli._handle_tui_todo_issues_action(session, 20, 70)

    assert errors == [], errors
    assert os.getcwd() == str(elsewhere)
