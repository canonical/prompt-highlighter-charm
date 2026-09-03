# prompt-highlighter

A Juju **subordinate** charm that gives every interactive shell on a machine a
colour-coded prompt showing which environment the machine belongs to.

```
[PRODUCTION] ubuntu:~/work$
```

## How it works

1. `config-changed` renders `templates/prompt.py.j2` into
   `/usr/local/bin/juju_dynamic_prompt.py`, baking in the configured
   environment label and colour.
2. The charm installs a *managed block* into `/etc/bash.bashrc` (and
   `/etc/zsh/zshrc` when `enable-zsh` is true) that calls that script to build
   the prompt before each command.

The managed block is delimited by markers, so re-applying config rewrites it in
place instead of appending a second copy, and `juju remove-application` (or
setting `enable-zsh=false`) removes it again. Nothing else in those files is
touched.

## Usage

```bash
charmcraft pack
juju deploy ubuntu
juju deploy ./prompt-highlighter_amd64.charm
juju integrate prompt-highlighter ubuntu
```

Configure it:

```bash
juju config prompt-highlighter environment-type=production prompt-color=red
juju config prompt-highlighter enable-zsh=false
```

## Configuration

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `environment-type` | string | `development` | Label shown in the prompt. 1-32 characters from `[A-Za-z0-9 _.:@+-]`. |
| `prompt-color` | string | `green` | One of `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`. |
| `enable-zsh` | boolean | `true` | Also configure `/etc/zsh/zshrc`. |

An invalid value puts the unit into `blocked` with a message naming the offending
option; the on-disk configuration is left untouched until it is corrected.

Changes apply to shells started after the config change — existing sessions keep
their old prompt until they are restarted.

## Development

```bash
tox -e lint          # ruff
tox -e unit          # pytest against ops.testing (Scenario)
charmcraft pack      # build the .charm
```
