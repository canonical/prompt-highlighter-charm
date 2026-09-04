# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the prompt-highlighter charm."""

import os
import pathlib
import re
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
        "principals": tmp_path / "var/lib/juju-prompt-highlighter/principals",
    }
    monkeypatch.setattr(charm_module, "PROMPT_SCRIPT", paths["script"])
    monkeypatch.setattr(charm_module, "BASH_RC", paths["bashrc"])
    monkeypatch.setattr(charm_module, "ZSH_RC", paths["zshrc"])
    monkeypatch.setattr(charm_module, "PRINCIPAL_DIR", paths["principals"])
    return paths


def make_ctx(unit_id=0):
    return testing.Context(
        PromptHighlighterCharm,
        charm_root=pathlib.Path(__file__).parents[2],
        unit_id=unit_id,
    )


@pytest.fixture
def ctx():
    return make_ctx()


PRINCIPAL = testing.SubordinateRelation(
    endpoint="juju-info",
    interface="juju-info",
    remote_app_name="ubuntu",
    remote_unit_id=3,
)


def run(ctx, event="config_changed", config=None, relations=(PRINCIPAL,)):
    state = testing.State(
        config=config or {},
        relations=set(relations),
        model=testing.Model(name="staging-mdl", type="lxd"),
        leader=True,
    )
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
    assert "for bash on ubuntu/3" in out.unit_status.message


def test_zsh_profile_is_not_created_when_disabled(ctx, fs):
    run(ctx, config={"enable-zsh": False})

    assert not fs["zshrc"].exists()


def test_remove_undoes_every_change(ctx, fs):
    run(ctx)
    fs["bashrc"].write_text("keep me\n" + fs["bashrc"].read_text())

    ctx.run(ctx.on.remove(), testing.State(leader=True, relations={PRINCIPAL}))

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


def strip_escapes(prompt: str) -> str:
    """Drop the colour codes, the window title and their zero-width markers."""
    prompt = re.sub(r"\033\][0-9]*;[^\007]*\007", "", prompt)
    return re.sub(r"\\\[|\\\]|%\{|%\}|\033\[[0-9;]*m", "", prompt)


def window_title(prompt: str) -> str | None:
    """Return the text of the OSC window-title sequence, or None if there is none."""
    match = re.search(r"\033\]0;([^\007]*)\007", prompt)
    return match.group(1) if match else None


def render_prompt(script, shell, cwd, status=0, term="xterm-256color", **overrides):
    env = {
        "USER": "ubuntu",
        "HOME": "/home/ubuntu",
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    if term is not None:
        env["TERM"] = term
    env.update(overrides)
    return subprocess.run(
        [sys.executable, str(script), shell, str(status)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout


# The prompt symbol tracks the effective user, so the expectation has to as well.
SYMBOL = "#" if os.geteuid() == 0 else "$"
HOSTNAME = os.uname().nodename


def context_line(prompt: str) -> str:
    return strip_escapes(prompt).split("\n")[0]


def test_script_records_model_and_principal_unit(ctx, fs):
    out = run(ctx)

    script = fs["script"].read_text()
    assert 'JUJU_MODEL = "staging-mdl"' in script
    assert f'PRINCIPAL_DIR = "{fs["principals"]}"' in script
    # The principal goes in this unit's own record, so sibling units on the
    # same machine do not overwrite each other's answer.
    assert (fs["principals"] / "prompt-highlighter-0").read_text() == "ubuntu/3\n"
    assert "on ubuntu/3" in out.unit_status.message


def test_relation_joined_refreshes_the_principal_unit(ctx, fs):
    # Install fires before the principal has joined: the segment is unknown.
    run(ctx, event="install", relations=())
    assert not (fs["principals"] / "prompt-highlighter-0").exists()

    state = testing.State(
        relations={PRINCIPAL},
        model=testing.Model(name="staging-mdl", type="lxd"),
        leader=True,
    )
    out = ctx.run(ctx.on.relation_joined(PRINCIPAL, remote_unit=3), state)

    assert (fs["principals"] / "prompt-highlighter-0").read_text() == "ubuntu/3\n"
    assert isinstance(out.unit_status, ops.ActiveStatus)


def test_prompt_puts_context_above_and_the_cursor_on_its_own_line(ctx, fs, tmp_path):
    run(ctx, config={"environment-type": "production", "prompt-color": "red"})

    out = strip_escapes(render_prompt(fs["script"], "bash", tmp_path))
    context, cursor = out.split("\n")

    assert context == f" PRODUCTION  staging-mdl \u00b7 ubuntu/3 \u00b7 {HOSTNAME}"
    assert cursor == f"ubuntu {tmp_path} {SYMBOL} "


def test_environment_badge_is_a_filled_block_not_bracketed_text(ctx, fs, tmp_path):
    run(ctx, config={"environment-type": "production", "prompt-color": "red"})

    out = render_prompt(fs["script"], "bash", tmp_path)

    # 41 is the red *background*: the label reads as a block before it is read
    # as text, and it survives on a monochrome terminal as reverse video.
    assert "\033[41m" in out
    assert "[PRODUCTION]" not in strip_escapes(out)


def test_grey_badge_lets_development_recede(ctx, fs, tmp_path):
    run(ctx, config={"environment-type": "development", "prompt-color": "grey"})

    assert "\033[100m" in render_prompt(fs["script"], "bash", tmp_path)


def test_window_title_carries_the_context(ctx, fs, tmp_path):
    run(ctx, config={"environment-type": "production"})

    title = window_title(render_prompt(fs["script"], "bash", tmp_path))

    assert title == f"[PRODUCTION] staging-mdl \u00b7 ubuntu/3 \u00b7 {HOSTNAME}"


@pytest.mark.parametrize("term", ["linux", "dumb", "", None])
def test_no_window_title_where_there_is_no_title_bar(ctx, fs, tmp_path, term):
    run(ctx)

    out = render_prompt(fs["script"], "bash", tmp_path, term=term)

    assert window_title(out) is None
    assert "\033]" not in out


def test_failed_command_is_flagged_on_the_context_line(ctx, fs, tmp_path):
    run(ctx)

    ok = context_line(render_prompt(fs["script"], "bash", tmp_path, status=0))
    failed = context_line(render_prompt(fs["script"], "bash", tmp_path, status=2))

    assert "\u2717" not in ok
    assert failed.endswith("\u2717 2")


def test_unknown_segments_are_omitted_not_blank(ctx, fs, tmp_path):
    run(ctx, event="install", relations=())

    context = context_line(render_prompt(fs["script"], "bash", tmp_path))

    assert " \u00b7  \u00b7 " not in context
    assert context == f" DEVELOPMENT  staging-mdl \u00b7 {HOSTNAME}"


def test_two_subordinate_units_on_one_machine_list_both_principals(fs, tmp_path):
    """Juju runs one subordinate unit per principal unit.

    A machine hosting two principals hosts two of our units, both writing the
    same files.
    """
    nova = testing.SubordinateRelation(
        endpoint="juju-info",
        interface="juju-info",
        remote_app_name="nova-compute",
        remote_unit_id=0,
    )
    ceph = testing.SubordinateRelation(
        endpoint="juju-info",
        interface="juju-info",
        remote_app_name="ceph-osd",
        remote_unit_id=2,
    )
    run(make_ctx(unit_id=0), relations=(nova,))
    run(make_ctx(unit_id=1), relations=(ceph,))

    assert sorted(p.name for p in fs["principals"].iterdir()) == [
        "prompt-highlighter-0",
        "prompt-highlighter-1",
    ]
    out = strip_escapes(render_prompt(fs["script"], "bash", tmp_path))
    assert "\u00b7 ceph-osd/2,nova-compute/0 \u00b7" in out


def test_long_principal_list_is_capped(fs, tmp_path):
    """The noisiest field is the one least worth the columns."""
    for index, app in enumerate(("ceph-osd", "nova-compute", "ovn-chassis", "telegraf")):
        relation = testing.SubordinateRelation(
            endpoint="juju-info",
            interface="juju-info",
            remote_app_name=app,
            remote_unit_id=index,
        )
        run(make_ctx(unit_id=index), relations=(relation,))

    out = strip_escapes(render_prompt(fs["script"], "bash", tmp_path))

    assert "ceph-osd/0,nova-compute/1 +2 more" in out
    assert "ovn-chassis" not in out


def test_separator_falls_back_to_ascii_when_the_locale_cannot_encode_it(ctx, fs, tmp_path):
    run(ctx)

    out = strip_escapes(render_prompt(fs["script"], "bash", tmp_path, PYTHONIOENCODING="ascii"))

    assert "\u00b7" not in out
    assert f"staging-mdl - ubuntu/3 - {HOSTNAME}" in out


def test_removing_one_unit_keeps_the_prompt_for_its_sibling(fs, tmp_path):
    other = testing.SubordinateRelation(
        endpoint="juju-info",
        interface="juju-info",
        remote_app_name="ceph-osd",
        remote_unit_id=2,
    )
    run(make_ctx(unit_id=0), relations=(PRINCIPAL,))
    run(make_ctx(unit_id=1), relations=(other,))

    make_ctx(unit_id=0).run(
        make_ctx(unit_id=0).on.remove(),
        testing.State(leader=True, relations={PRINCIPAL}),
    )

    # Our record is gone; the shared files stay for prompt-highlighter/1.
    assert not (fs["principals"] / "prompt-highlighter-0").exists()
    assert fs["script"].exists()
    assert charm_module.BLOCK_START in fs["bashrc"].read_text()
    out = strip_escapes(render_prompt(fs["script"], "bash", tmp_path))
    assert "\u00b7 ceph-osd/2 \u00b7" in out


def test_last_unit_off_the_machine_cleans_everything(ctx, fs):
    run(ctx)

    ctx.run(ctx.on.remove(), testing.State(leader=True, relations={PRINCIPAL}))

    assert not fs["script"].exists()
    assert not fs["principals"].exists()
    assert charm_module.BLOCK_START not in fs["bashrc"].read_text()


def test_generated_script_wraps_escapes_for_bash(ctx, fs, tmp_path):
    run(ctx, config={"environment-type": "production", "prompt-color": "red"})

    out = render_prompt(fs["script"], "bash", tmp_path)

    assert out.startswith("\\[\033]0;")
    assert out.endswith("\\[\033[0m\\] ")
    assert " PRODUCTION  staging-mdl \u00b7 ubuntu/3 \u00b7 " in strip_escapes(out)


def test_generated_script_wraps_escapes_for_zsh(ctx, fs, tmp_path):
    run(ctx, config={"environment-type": "staging", "prompt-color": "blue"})

    out = render_prompt(fs["script"], "zsh", tmp_path)

    assert out.startswith("%{\033]0;")
    assert out.endswith("%{\033[0m%} ")
    assert " STAGING  staging-mdl \u00b7 ubuntu/3 \u00b7 " in strip_escapes(out)


def test_generated_script_escapes_prompt_metacharacters(ctx, fs, tmp_path):
    run(ctx)
    awkward = tmp_path / "100%_back\\slash"
    awkward.mkdir()

    zsh = render_prompt(fs["script"], "zsh", awkward)
    bash = render_prompt(fs["script"], "bash", awkward)

    assert "100%%_back\\slash" in zsh
    assert "100%_back\\\\slash" in bash


# A prompt string is re-expanded by the shell on every draw, so anything the
# charm puts in it that a local user can name is an execution vector.
INJECTIONS = [
    "$(touch pwned)",
    "`touch pwned`",
    "${IFS}",
    "$USER",
]


@pytest.mark.parametrize("payload", INJECTIONS)
def test_bash_prompt_does_not_expand_hostile_directory_names(ctx, fs, tmp_path, payload):
    """Any user can create a directory; only its name should reach the prompt.

    Left unescaped, a `cd` into a directory named `$(...)` runs it as whoever
    is at the keyboard, the next time the prompt is drawn.
    """
    run(ctx)
    hostile = tmp_path / payload
    hostile.mkdir()

    out = render_prompt(fs["script"], "bash", hostile)

    expanded = subprocess.run(
        ["bash", "-c", 'PS1="$1"; printf "%s" "${PS1@P}"', "_", out],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=True,
    ).stdout
    assert not (tmp_path / "pwned").exists()
    # And the name is still shown as it was written, not swallowed.
    assert payload in expanded


@pytest.mark.parametrize(
    ("payload", "shown"),
    [
        ("$(touch pwned)", "?(touch pwned)"),
        ("`touch pwned`", "?touch pwned?"),
        ("${IFS}", "?{IFS}"),
        ("$USER", "?USER"),
    ],
)
def test_zsh_prompt_carries_no_expandable_text(ctx, fs, tmp_path, payload, shown):
    """Check that nothing zsh could expand survives into the prompt.

    Zsh expands $ and ` only under PROMPT_SUBST, which is the operator's own
    setting and unknowable here, so neither may reach the prompt at all.
    """
    run(ctx)
    hostile = tmp_path / payload
    hostile.mkdir()

    # The last two fields of the cursor line are the symbol and its space.
    out = strip_escapes(render_prompt(fs["script"], "zsh", hostile))
    path = out.split("\n")[1].rsplit(" ", 2)[0]

    assert path == f"ubuntu {hostile.parent}/{shown}"


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("a\033[2Jb", "a?[2Jb"),
        # C1: some terminals still decode U+0080-U+009F as control sequences.
        ("a\u009bBc", "a?Bc"),
    ],
)
def test_control_characters_never_reach_the_terminal(ctx, fs, tmp_path, name, shown):
    """A directory name can hold an escape sequence; the prompt may not pass it on."""
    run(ctx)
    hostile = tmp_path / name
    hostile.mkdir()

    out = render_prompt(fs["script"], "bash", hostile)

    assert "\033[2J" not in out
    assert "\u009b" not in out
    assert shown in strip_escapes(out)


def test_backslashes_in_a_path_are_not_read_as_bash_prompt_escapes(ctx, fs, tmp_path):
    r"""Bash decodes \u, \h and \w in a prompt; a path containing them is text."""
    run(ctx)
    hostile = tmp_path / r"\u\h\w"
    hostile.mkdir()

    out = render_prompt(fs["script"], "bash", hostile)

    expanded = subprocess.run(
        ["bash", "-c", 'PS1="$1"; printf "%s" "${PS1@P}"', "_", out],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert r"\u\h\w" in expanded


def test_a_hostile_username_cannot_run_commands(ctx, fs, tmp_path):
    """$USER is read from the environment, which the charm does not control."""
    run(ctx)

    out = render_prompt(fs["script"], "bash", tmp_path, USER="$(touch pwned)")

    subprocess.run(
        ["bash", "-c", 'PS1="$1"; printf "%s" "${PS1@P}"', "_", out],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=True,
    )
    assert not (tmp_path / "pwned").exists()
