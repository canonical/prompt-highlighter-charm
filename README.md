# prompt-highlighter

A Juju **subordinate** charm that gives every interactive shell on a machine a
two-line prompt saying which environment, model, principal unit and host you are
logged into — so that a `systemctl stop` typed into the wrong terminal is visibly
wrong before Enter is pressed.

```
 PRODUCTION  prod-openstack · nova-compute/3 · juju-a1b2c3-7
root ~ #
```

The environment label is a **reverse-video badge** — ` PRODUCTION ` is white on a
red field — rather than bracketed text. A block of colour registers before it is
read, and it still reads as inverted on a terminal with no colour at all, which
bracketed text does not. Everything after the badge is deliberately quieter, and
the principal list — the longest field and the least urgent — is quietest of all.

The cursor gets a line to itself, so the whole terminal width is yours to type in
and your commands stay left-aligned in the scrollback. Nine columns are spent
before the cursor, against seventy-nine for a single-line prompt carrying the
same five facts.

| Segment | Source | Resolved |
| --- | --- | --- |
| Environment badge | `environment-type` + `prompt-color` config | at hook time |
| Juju model | Juju | at hook time |
| Principal units | the record directory, see below | at prompt time |
| Hostname | `os.uname()` | at prompt time |
| User and directory | the environment and `getcwd()` | at prompt time |

Three details earn their keep:

- **`#` for root, `$` for everyone else.** The Unix convention, and it costs no
  columns.
- **`✗ 1` after a failed command**, in red, on the context line — and nothing at
  all when the last command succeeded.
- **The principal list stops at two**, then says `+2 more`. A machine with a deep
  stack of principals cannot push your cursor off the screen.

Pick `prompt-color=grey` for development. A badge only means something if the
unremarkable environments look unremarkable; if every prompt shouts, none of them
does.

> **The prompt is advisory, not a control.** It makes a wrong terminal *look*
> wrong; it authenticates nobody and prevents no command. Keep your real
> controls independent of it.

## The window title

The same context is written to the terminal window title:

```
[PRODUCTION] prod-openstack · nova-compute/3 · juju-a1b2c3-7
```

The escape sequence is written by the shell **on the unit** but interpreted by
the terminal emulator **on your desk**, so the title follows a plain `ssh` or a
`juju ssh` all the way back to the window you are looking at. That is the same
mechanism Ubuntu's own `/etc/skel/.bashrc` uses for its `user@host: dir` title,
and because this charm sets `PS1` from `PROMPT_COMMAND`, it wins over that one.

Two things to know:

- Nothing is written when `TERM` has no title bar to set — `linux`, `dumb`, a
  serial or `virsh` console.
- Inside `tmux`, panes cannot rename the window unless the operator has set
  `set-titles on` and `allow-rename on`; both are off by default. That is the
  user's own tmux config, not something the charm can set.

## How it works

1. `config-changed` (and `juju-info-relation-joined`) writes this unit's
   principal name to `/var/lib/juju-prompt-highlighter/principals/<unit>`, then
   renders `templates/prompt.py.j2` into
   `/usr/local/bin/juju_dynamic_prompt.py`, baking in the configured
   environment label and colour and the Juju model name.
2. The charm installs a *managed block* into `/etc/bash.bashrc` (and
   `/etc/zsh/zshrc` when `enable-zsh` is true) that calls that script to build
   the prompt before each command, passing it the exit status of the command you
   just ran.

The hook is *prepended* to any existing `PROMPT_COMMAND` rather than appended, so
that it still sees the exit status of your own last command rather than that of
somebody else's hook.

The managed block is delimited by markers, so re-applying config rewrites it in
place instead of appending a second copy, and `juju remove-application` (or
setting `enable-zsh=false`) removes it again. Nothing else in those files is
touched.

## Usage

```bash
charmcraft pack
juju deploy ubuntu
juju deploy ./prompt-highlighter_ubuntu@24.04-amd64.charm
juju integrate prompt-highlighter ubuntu
```

`charmcraft pack` builds one charm file per base — `ubuntu@22.04`, `ubuntu@24.04`
and `ubuntu@26.04`, amd64 — so deploy the one matching the base of the machines
its principal runs on. A subordinate has to share its principal's base.

```bash
charmcraft pack --platform ubuntu@22.04:amd64   # just the one you need
```

Each file ships a virtual environment built by that series' own Python (3.10,
3.12 and 3.14 respectively), which is why they are not interchangeable.

Configure it:

```bash
juju config prompt-highlighter environment-type=production prompt-color=red
juju config prompt-highlighter environment-type=development prompt-color=grey
juju config prompt-highlighter enable-zsh=false
```

## Configuration

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `environment-type` | string | `development` | Label shown in the badge. 1-32 characters from `[A-Za-z0-9 _.:@+-]`, starting with a letter, digit or underscore. |
| `prompt-color` | string | `green` | Badge background: `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`, `grey`. |
| `enable-zsh` | boolean | `true` | Also configure `/etc/zsh/zshrc`. |

An invalid value puts the unit into `blocked` with a message naming the offending
option; the on-disk configuration is left untouched until it is corrected.

Badge backgrounds use the basic ANSI codes so they land correctly on any
terminal; the dimmer context colours use the 256-colour palette. Where the
locale cannot encode `·` and `✗`, the script falls back to `-` and `x` rather
than printing replacement characters into every prompt.

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

Beyond two principals the badge line summarises rather than lists:

```
 PRODUCTION  prod-openstack · ceph-osd/2,nova-compute/0 +2 more · juju-a1b2c3-7
```

Each unit's `juju status` message still names its own principal.

The principal unit is only known once the `juju-info` relation has joined. A
prompt rendered before that (during `install`) simply omits the segment along
with its separator, and the relation-joined hook re-renders it — no blank ` ·  · `
gap appears.

Two known limits: a unit whose `remove` hook fails leaves a stale record (delete
the file to fix), and deploying *two* `prompt-highlighter` applications to one
machine makes them fight over the label and colour, since those are baked into the
one shared script.

Changes apply to shells started after the config change — existing sessions keep
their old prompt until they are restarted.

## Security

One control is worth knowing about from the outside: a shell re-expands its
prompt on every draw, so anything variable that reaches it — a directory name,
`$USER`, a hostname — could otherwise be *executed* as whoever is at the
keyboard. The generated script neutralises command substitution and strips
control characters before either can happen, per shell.

## Development

```bash
tox -e lint          # ruff
tox -e unit          # pytest against ops.testing (Scenario)
charmcraft pack      # build one .charm per base
```

Runtime dependencies live in `pyproject.toml` and are pinned in `uv.lock`, which
is committed. `charmcraft pack` installs the charm's venv from that lock, and the
tox environments sync from it too, so the tests run against what gets shipped.
Change a dependency with `uv add` / `uv lock` and commit the lock alongside it.
Keep the charm code within Python 3.10: that is what `ubuntu@22.04` ships, and
`requires-python`/ruff's `target-version` are set to match.
