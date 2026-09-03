# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the prompt-highlighter charm."""

import pathlib
import subprocess
import sys

import ops
import pytest
from ops import testing

import charm as charm_module
from charm import PromptHighlighterCharm


@pytest.fixture
def fs(tmp_path, monkeypatch):
    """Redirect every path the charm writes to into a temporary tree."""
    paths = {
        "script": tmp_path / "usr/local/bin/juju_dynamic_prompt.py",
        "bashrc": tmp_path / "etc/bash.bashrc",
        "zshrc": tmp_path / "etc/zsh/zshrc",
    }
    monkeypatch.setattr(charm_module, "PROMPT_SCRIPT", paths["script"])
    monkeypatch.setattr(charm_module, "BASH_RC", paths["bashrc"])
    monkeypatch.setattr(charm_module, "ZSH_RC", paths["zshrc"])
    return paths


@pytest.fixture
def ctx():
    return testing.Context(
        PromptHighlighterCharm,
        charm_root=pathlib.Path(__file__).parents[2],
    )


def run(ctx, event="config_changed", config=None):
    state = testing.State(config=config or {}, leader=True)
    return ctx.run(getattr(ctx.on, event)(), state)


def test_config_changed_writes_script_and_profiles(ctx, fs):
    out = run(ctx, config={"environment-type": "production", "prompt-color": "red"})

    assert isinstance(out.unit_status, ops.ActiveStatus)
    assert "production" in out.unit_status.message
    assert "red" in out.unit_status.message

    script = fs["script"].read_text()
    assert 'ENVIRONMENT_LABEL = "production"' in script
    assert 'PROMPT_COLOR = "red"' in script
    assert fs["script"].stat().st_mode & 0o777 == 0o755

    for rc in (fs["bashrc"], fs["zshrc"]):
        body = rc.read_text()
        assert charm_module.BLOCK_START in body
        assert charm_module.BLOCK_END in body
        assert str(fs["script"]) in body


def test_install_applies_configuration(ctx, fs):
    out = run(ctx, event="install")

    assert isinstance(out.unit_status, ops.ActiveStatus)
    assert fs["script"].exists()


def test_existing_profile_content_is_preserved(ctx, fs):
    fs["bashrc"].parent.mkdir(parents=True)
    fs["bashrc"].write_text("# operator's own setting\nexport EDITOR=vim\n")

    run(ctx)

    body = fs["bashrc"].read_text()
    assert body.startswith("# operator's own setting\nexport EDITOR=vim\n")
    assert charm_module.BLOCK_START in body


def test_reapplying_does_not_duplicate_the_block(ctx, fs):
    run(ctx)
    first = fs["bashrc"].read_text()
    run(ctx, config={"prompt-color": "blue"})
    second = fs["bashrc"].read_text()

    assert first.count(charm_module.BLOCK_START) == 1
    assert second.count(charm_module.BLOCK_START) == 1
    assert 'PROMPT_COLOR = "blue"' in fs["script"].read_text()


def test_disabling_zsh_removes_only_the_zsh_block(ctx, fs):
    run(ctx, config={"enable-zsh": True})
    assert charm_module.BLOCK_START in fs["zshrc"].read_text()

    out = run(ctx, config={"enable-zsh": False})

    assert charm_module.BLOCK_START not in fs["zshrc"].read_text()
    assert charm_module.BLOCK_START in fs["bashrc"].read_text()
    assert out.unit_status.message.endswith("for bash")


def test_zsh_profile_is_not_created_when_disabled(ctx, fs):
    run(ctx, config={"enable-zsh": False})

    assert not fs["zshrc"].exists()


def test_remove_undoes_every_change(ctx, fs):
    run(ctx)
    fs["bashrc"].write_text("keep me\n" + fs["bashrc"].read_text())

    ctx.run(ctx.on.remove(), testing.State(leader=True))

    assert not fs["script"].exists()
    assert fs["bashrc"].read_text() == "keep me\n"
    assert fs["zshrc"].read_text() == ""


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"prompt-color": "octarine"}, "invalid prompt-color"),
        ({"prompt-color": ""}, "invalid prompt-color"),
        ({"environment-type": ""}, "invalid environment-type"),
        ({"environment-type": "prod\nrm -rf /"}, "invalid environment-type"),
        ({"environment-type": "a" * 33}, "invalid environment-type"),
    ],
)
def test_invalid_config_blocks_without_touching_the_disk(ctx, fs, config, expected):
    out = run(ctx, config=config)

    assert isinstance(out.unit_status, ops.BlockedStatus)
    assert expected in out.unit_status.message
    assert not fs["script"].exists()
    assert not fs["bashrc"].exists()


def test_config_values_are_normalised(ctx, fs):
    run(ctx, config={"environment-type": " staging ", "prompt-color": " GREEN "})

    script = fs["script"].read_text()
    assert 'ENVIRONMENT_LABEL = "staging"' in script
    assert 'PROMPT_COLOR = "green"' in script


def render_prompt(script: pathlib.Path, shell: str, cwd: pathlib.Path) -> str:
    return subprocess.run(
        [sys.executable, str(script), shell],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={"USER": "ubuntu", "HOME": "/home/ubuntu", "PATH": "/usr/bin:/bin"},
    ).stdout


def test_generated_script_wraps_escapes_for_bash(ctx, fs, tmp_path):
    run(ctx, config={"environment-type": "production", "prompt-color": "red"})

    out = render_prompt(fs["script"], "bash", tmp_path)

    assert out.startswith("\\[\033[0;31m\\]")
    assert out.endswith("\\[\033[0m\\]")
    assert "[PRODUCTION] ubuntu:" in out


def test_generated_script_wraps_escapes_for_zsh(ctx, fs, tmp_path):
    run(ctx, config={"environment-type": "staging", "prompt-color": "blue"})

    out = render_prompt(fs["script"], "zsh", tmp_path)

    assert out.startswith("%{\033[0;34m%}")
    assert out.endswith("%{\033[0m%}")
    assert "[STAGING] ubuntu:" in out


def test_generated_script_escapes_prompt_metacharacters(ctx, fs, tmp_path):
    run(ctx)
    awkward = tmp_path / "100%_back\\slash"
    awkward.mkdir()

    zsh = render_prompt(fs["script"], "zsh", awkward)
    bash = render_prompt(fs["script"], "bash", awkward)

    assert "100%%_back\\slash" in zsh
    assert "100%_back\\\\slash" in bash
