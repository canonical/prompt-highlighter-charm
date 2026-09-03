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

When a machine hosts more than one principal, all of them are listed:

```
[PRODUCTION] - prod-openstack - ceph-osd/2,nova-compute/0 - juju-a1b2c3-7 root:~$
```

## How it works

1. `config-changed` (and `juju-info-relation-joined`) writes this unit's
   principal name to `/var/lib/juju-prompt-highlighter/principals/<unit>`, then
   renders `templates/prompt.py.j2` into
   `/usr/local/bin/juju_dynamic_prompt.py`, baking in the configured
   environment label and colour and the Juju model name.
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

## Multiple principals on one machine

Juju runs **one subordinate unit per principal unit**, not one per machine. Relate
this charm to both `nova-compute` and `ceph-osd` and a machine hosting one unit of
each gets *two* `prompt-highlighter` units, both running hooks against the same
`/etc/bash.bashrc` and the same script.

A unit's `juju-info` relation only ever contains its own principal, so no single
unit can see the others. Instead each unit writes one record naming its principal:

```
/var/lib/juju-prompt-highlighter/principals/
├── prompt-highlighter-0   -> nova-compute/0
└── prompt-highlighter-1   -> ceph-osd/2
```

That directory *is* the set of principals on the machine, since only units sharing
the machine can write to it. The prompt script reads it on every render, so:

- the generated script and the profile snippets are identical for every unit, and
  concurrent writes from siblings are idempotent rather than a last-writer-wins
  fight;
- a principal added or removed later shows up in shells that are **already open**,
  with no restart;
- `juju remove-relation` for one principal removes only that unit's record. The
  script and profile snippets are torn down by the last unit to leave the machine,
  so a surviving sibling keeps its prompt.

Each unit's `juju status` message still names its own principal.

The principal unit is only known once the `juju-info` relation has joined. A
prompt rendered before that (during `install`) simply omits the segment, and the
relation-joined hook re-renders it — no blank ` -  - ` gap appears.

Two known limits: a unit whose `remove` hook fails leaves a stale record (delete
the file to fix), and deploying *two* `prompt-highlighter` applications to one
machine makes them fight over the label and colour, since those are baked into the
one shared script.

Changes apply to shells started after the config change — existing sessions keep
their old prompt until they are restarted.

## Development

```bash
tox -e lint          # ruff
tox -e unit          # pytest against ops.testing (Scenario)
charmcraft pack      # build the .charm
```
