# prompt-highlighter

A Juju **subordinate** charm that gives every interactive shell on a machine a
colour-coded prompt saying which environment, model, principal unit and host you
are logged into.

```
[PRODUCTION] - prod-openstack - nova-compute/3 - juju-a1b2c3-7 root:~$
 \_ label      \_ Juju model    \_ principal    \_ hostname
```

The label and colour come from config; the model and principal unit come from
Juju; the hostname is read at prompt time, so it is the name of the machine or
LXD container you are actually inside.

## How it works

1. `config-changed` (and `juju-info-relation-joined`) renders
   `templates/prompt.py.j2` into `/usr/local/bin/juju_dynamic_prompt.py`,
   baking in the configured environment label and colour, the Juju model name
   and the principal unit name.
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

The principal unit is only known once the `juju-info` relation has joined. A
prompt rendered before that (during `install`) simply omits the segment, and the
relation-joined hook re-renders it — no blank ` -  - ` gap appears.

Changes apply to shells started after the config change — existing sessions keep
their old prompt until they are restarted.

## Development

```bash
tox -e lint          # ruff
tox -e unit          # pytest against ops.testing (Scenario)
charmcraft pack      # build the .charm
```
