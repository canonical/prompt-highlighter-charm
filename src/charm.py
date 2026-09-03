#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Subordinate charm that installs a colour-coded, system-wide shell prompt.

The charm renders a small Python script from ``templates/prompt.py.j2`` and
installs it as ``/usr/local/bin/juju_dynamic_prompt.py``. Global shell profiles
then call that script to build the prompt on every command. Everything written
outside the charm directory is wrapped in managed markers so that it can be
updated in place and removed cleanly.
"""

import contextlib
import dataclasses
import logging
import pathlib
import re
import typing

import ops
from jinja2 import Environment, FileSystemLoader, StrictUndefined

logger = logging.getLogger(__name__)

# The container-scoped endpoint that ties this subordinate to its principal.
PRINCIPAL_ENDPOINT = "juju-info"

PROMPT_SCRIPT = pathlib.Path("/usr/local/bin/juju_dynamic_prompt.py")
# One file per subordinate unit on this machine, named after that unit and
# holding its principal's name. Juju runs one subordinate unit per principal
# unit, so several of them can share a machine; the directory listing is
# therefore the set of principals here, and needs no cross-unit coordination.
PRINCIPAL_DIR = pathlib.Path("/var/lib/juju-prompt-highlighter/principals")
BASH_RC = pathlib.Path("/etc/bash.bashrc")
ZSH_RC = pathlib.Path("/etc/zsh/zshrc")

BLOCK_START = "# BEGIN prompt-highlighter charm (managed) -- do not edit"
BLOCK_END = "# END prompt-highlighter charm"

VALID_COLORS = ("red", "green", "yellow", "blue", "magenta", "cyan", "white")
ENV_LABEL = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9 _.:@+-]{0,31}$")


def _bash_snippet() -> str:
    """Return the Bash profile snippet that installs the prompt hook."""
    return f"""\
case $- in
    *i*) ;;
    *) return ;;
esac
_juju_prompt_highlighter() {{
    PS1="$({PROMPT_SCRIPT} bash)"
}}
case "$PROMPT_COMMAND" in
    *_juju_prompt_highlighter*) ;;
    "") PROMPT_COMMAND=_juju_prompt_highlighter ;;
    *) PROMPT_COMMAND="_juju_prompt_highlighter;$PROMPT_COMMAND" ;;
esac
"""


def _zsh_snippet() -> str:
    """Return the Zsh profile snippet that installs the prompt hook."""
    return f"""\
_juju_prompt_highlighter() {{
    PROMPT="$({PROMPT_SCRIPT} zsh)"
}}
autoload -Uz add-zsh-hook
add-zsh-hook precmd _juju_prompt_highlighter
"""


class ConfigError(Exception):
    """Raised when the Juju configuration cannot be applied as given."""


@dataclasses.dataclass(frozen=True)
class PromptConfig:
    """Validated view of the charm's configuration options."""

    environment_type: str
    prompt_color: str
    enable_zsh: bool

    @classmethod
    def load(cls, config: ops.ConfigData) -> "PromptConfig":
        """Read and validate the charm config, raising ConfigError if unusable."""
        environment_type = typing.cast(str, config["environment-type"]).strip()
        if not ENV_LABEL.match(environment_type):
            raise ConfigError(
                f"invalid environment-type {environment_type!r}: expected 1-32 "
                "characters from [A-Za-z0-9 _.:@+-]"
            )

        prompt_color = typing.cast(str, config["prompt-color"]).strip().lower()
        if prompt_color not in VALID_COLORS:
            raise ConfigError(
                f"invalid prompt-color {prompt_color!r}: expected one of "
                f"{', '.join(VALID_COLORS)}"
            )

        return cls(
            environment_type=environment_type,
            prompt_color=prompt_color,
            enable_zsh=typing.cast(bool, config["enable-zsh"]),
        )


class PromptHighlighterCharm(ops.CharmBase):
    """Keep the on-disk prompt configuration in sync with the Juju config."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        for event in (
            self.on.install,
            self.on.start,
            self.on.upgrade_charm,
            self.on.config_changed,
            # The principal unit name is only knowable once it has joined, so
            # re-render then too. relation-broken is skipped: the subordinate
            # unit is being torn down at that point anyway.
            self.on[PRINCIPAL_ENDPOINT].relation_joined,
            self.on[PRINCIPAL_ENDPOINT].relation_changed,
        ):
            framework.observe(event, self._reconcile)
        framework.observe(self.on.remove, self._on_remove)

    def _reconcile(self, _: ops.EventBase) -> None:
        """Render the prompt script and (re)apply the shell profile snippets."""
        try:
            config = PromptConfig.load(self.config)
        except ConfigError as exc:
            self.unit.status = ops.BlockedStatus(str(exc))
            return

        self.unit.status = ops.MaintenanceStatus("Applying prompt configuration")
        principal = self._principal_unit()
        try:
            # Publish only our own principal. The script and the profile
            # snippets are identical for every unit of this application, so
            # concurrent writes from sibling units are harmless.
            record = self._principal_record()
            if principal:
                _write_file(record, f"{principal}\n", 0o644)
            else:
                record.unlink(missing_ok=True)
            _write_file(PROMPT_SCRIPT, self._render_script(config), 0o755)
            _apply_block(BASH_RC, _bash_snippet(), enabled=True)
            _apply_block(ZSH_RC, _zsh_snippet(), enabled=config.enable_zsh)
        except OSError as exc:
            logger.exception("Failed to apply prompt configuration")
            self.unit.status = ops.BlockedStatus(f"Failed to apply prompt: {exc}")
            return

        shells = "bash and zsh" if config.enable_zsh else "bash"
        alongside = f" on {principal}" if principal else ""
        self.unit.status = ops.ActiveStatus(
            f"Prompt set to {config.environment_type} "
            f"({config.prompt_color}) for {shells}{alongside}"
        )

    def _on_remove(self, _: ops.RemoveEvent) -> None:
        """Undo every change the charm made outside its own directory."""
        self.unit.status = ops.MaintenanceStatus("Removing prompt configuration")
        try:
            self._principal_record().unlink(missing_ok=True)
            remaining = _principal_records()
            if remaining:
                # Other subordinate units still share this machine and are still
                # using the script and the profile snippets. Only our record goes.
                logger.info(
                    "Keeping shell configuration for %d other unit(s) here: %s",
                    len(remaining),
                    ", ".join(remaining),
                )
                return
            _apply_block(BASH_RC, _bash_snippet(), enabled=False)
            _apply_block(ZSH_RC, _zsh_snippet(), enabled=False)
            PROMPT_SCRIPT.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                PRINCIPAL_DIR.rmdir()
                PRINCIPAL_DIR.parent.rmdir()
        except OSError:
            # Removal must not block teardown; the operator can clean up by hand.
            logger.exception("Failed to remove prompt configuration")

    def _principal_record(self) -> pathlib.Path:
        """Return this unit's own principal record file."""
        return PRINCIPAL_DIR / self.unit.name.replace("/", "-")

    def _principal_unit(self) -> str | None:
        """Return the principal unit this subordinate shares a machine with.

        The juju-info relation is container-scoped, so it holds exactly one
        remote unit. It is absent until relation-joined has fired.
        """
        for relation in self.model.relations[PRINCIPAL_ENDPOINT]:
            for unit in relation.units:
                return unit.name
        return None

    def _render_script(self, config: PromptConfig) -> str:
        """Render templates/prompt.py.j2 for the given configuration."""
        env = Environment(
            loader=FileSystemLoader(self.charm_dir / "templates"),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,  # The output is Python source, not markup.
        )
        template = env.get_template("prompt.py.j2")
        return template.render(
            environment_type=config.environment_type,
            prompt_color=config.prompt_color,
            juju_model=self.model.name,
            principal_dir=str(PRINCIPAL_DIR),
        )


def _principal_records() -> list[str]:
    """Return the names of every subordinate unit with a record on this machine."""
    try:
        return sorted(path.name for path in PRINCIPAL_DIR.iterdir() if path.is_file())
    except OSError:
        return []


def _write_file(path: pathlib.Path, content: str, mode: int) -> None:
    """Write path atomically, so a live shell never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.juju-tmp")
    tmp.write_text(content)
    tmp.chmod(mode)
    tmp.replace(path)


def _strip_block(text: str) -> str:
    """Return text with any previously managed block removed."""
    kept: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        if not skipping and line.strip() == BLOCK_START:
            skipping = True
        elif skipping and line.strip() == BLOCK_END:
            skipping = False
        elif not skipping:
            kept.append(line)
    return "".join(kept)


def _apply_block(path: pathlib.Path, snippet: str, enabled: bool) -> None:
    """Add, refresh or remove the charm's managed block in a shell profile."""
    if not path.exists():
        if not enabled:
            return
        logger.info("Creating %s; it did not exist yet", path)
        original = None
        body = ""
    else:
        original = path.read_text()
        body = _strip_block(original)

    if enabled:
        if body and not body.endswith("\n"):
            body += "\n"
        body += f"{BLOCK_START}\n{snippet.strip()}\n{BLOCK_END}\n"

    if body != original:
        _write_file(path, body, 0o644)


if __name__ == "__main__":  # pragma: nocover
    ops.main(PromptHighlighterCharm)
