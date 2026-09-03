# prompt-highlighter — Specification

Reverse-engineered from the code at `aa3621a` ("Rework charm"). Every statement
below is grounded in a file and line reference; inferences and open questions are
kept in [§10](#10-uncertainties-and-discrepancies) rather than mixed into the
requirements.

Requirements use [EARS](https://alistairmavin.com/ears/) phrasing:
*ubiquitous* ("The charm shall …"), *event-driven* ("When X, the charm shall …"),
*state-driven* ("While X, …"), *optional* ("Where X, …").

---

## 1. Purpose

`prompt-highlighter` is a Juju **subordinate** charm that makes every interactive
shell on a machine announce, in colour, which environment and which Juju
model/unit the operator is logged into — so that a `systemctl stop` typed into
the wrong terminal is visibly wrong before Enter is pressed.

The rendered prompt has the shape (`templates/prompt.py.j2:85-98`):

```
[PRODUCTION] - prod-openstack - nova-compute/3 - juju-a1b2c3-7 root:~$
 \_ label      \_ Juju model    \_ principal    \_ hostname   \_ user:cwd
```

## 2. Scope and method

**In scope:** the whole repository — charm metadata, charm code, prompt
template, test suite, tooling.

**Method:** all source files were read in full (`src/charm.py` 233 lines,
`templates/prompt.py.j2` 103 lines, `tests/unit/test_charm.py` 251 lines,
`charmcraft.yaml`, `pyproject.toml`, `tox.ini`, `requirements.txt`). No file in
the tree was left unread, so the observations are complete rather than sampled.

**Out of scope:** the packed `prompt-highlighter_amd64.charm` build artefact
(binary, gitignored) and the `.serena/` / `.pytest_cache/` tool directories.

## 3. Technology stack

| Concern | Choice | Evidence |
| --- | --- | --- |
| Charm framework | `ops ~= 3.7` | `requirements.txt:1` |
| Templating | `jinja2 ~= 3.1`, `StrictUndefined`, autoescape off | `requirements.txt:2`, `src/charm.py:173-178` |
| Charm format | `charmcraft.yaml` unified (no `metadata.yaml`/`config.yaml`) | `charmcraft.yaml:4-5` |
| Base / platform | `ubuntu@24.04`, `amd64` only | `charmcraft.yaml:23-25` |
| Deployment model | subordinate, container-scoped `juju-info` | `charmcraft.yaml:21,29-32` |
| Tests | `pytest` + `ops.testing` (Scenario), state-transition style | `tox.ini:22-28`, `tests/unit/test_charm.py:14` |
| Lint/format | `ruff` (line length 99, py312, `E,F,W,I,N,D,UP,B,C4,RUF`) | `pyproject.toml:4-13` |
| Runtime deps on the unit | none — no packages installed, no network access | absence of any `apt`/`subprocess` call in `src/charm.py` |

## 4. Repository layout

```
charmcraft.yaml          charm metadata + config schema + build parts
requirements.txt         runtime deps vendored into the charm
pyproject.toml           pytest + ruff configuration
tox.ini                  lint / unit environments
src/charm.py             the operator: validation, rendering, file management
templates/prompt.py.j2   the artefact rendered onto the unit
tests/unit/test_charm.py 15 tests: charm state transitions + real prompt output
```

`parts.charm.prime` uses exclusion-only filters, so everything not listed is
primed into the charm — notably `templates/`, which the charm reads at runtime
via `self.charm_dir / "templates"` (`charmcraft.yaml:61-66`, `src/charm.py:174`).

## 5. Architecture and data flow

Two artefacts are written outside the charm directory, both derived entirely
from Juju state:

```
Juju config ──┐
              ├─► PromptConfig.load() ──► validated ──┐
model name ───┤        (charm.py:80-101)              │
principal ────┘                                       ▼
                                        Jinja render (charm.py:171-185)
                                                      │
                                                      ▼
                            /usr/local/bin/juju_dynamic_prompt.py   (0755)
                                                      ▲
                                                      │ exec'd per prompt
                            /etc/bash.bashrc  managed block  (0644)
                            /etc/zsh/zshrc    managed block  (0644, optional)
```

**Render time vs. prompt time.** The environment label, colour, model name and
principal unit are *baked in* at hook time as Python literals
(`templates/prompt.py.j2:13-16`). The user, hostname and working directory are
resolved *per prompt* (`prompt.py.j2:47-77`), so a renamed host or a container
cloned from an image reports itself correctly without a re-render.

**Managed block.** Both shell profiles are edited through one primitive,
`_apply_block` (`src/charm.py:211-229`), which strips any existing block
delimited by `BLOCK_START`/`BLOCK_END` (`src/charm.py:32-33`) and appends a fresh
one. This makes add / update / remove a single idempotent operation and confines
the charm's footprint in operator-owned files to the marked region.

**Shell hooks.** Bash installs a `PROMPT_COMMAND` function guarded by an
interactive-shell check (`src/charm.py:39-54`); Zsh registers a `precmd` hook via
`add-zsh-hook` (`src/charm.py:57-65`). Both call the generated script with the
shell name as `argv[1]`, which selects the correct zero-width escape delimiters.

## 6. Configuration surface

| Option | Type | Default | Validation | Evidence |
| --- | --- | --- | --- | --- |
| `environment-type` | string | `development` | stripped, then `^[A-Za-z0-9_][A-Za-z0-9 _.:@+-]{0,31}$` | `charmcraft.yaml:36-42`, `src/charm.py:36,83-88` |
| `prompt-color` | string | `green` | stripped, lowercased, ∈ {red, green, yellow, blue, magenta, cyan, white} | `charmcraft.yaml:43-48`, `src/charm.py:35,90-95` |
| `enable-zsh` | boolean | `true` | none | `charmcraft.yaml:49-54`, `src/charm.py:100` |

There is no `enable-bash` counterpart: Bash is always configured
(`src/charm.py:134`).

## 7. Observed requirements

### 7.1 Lifecycle

- **REQ-1** — When any of `install`, `start`, `upgrade-charm`, `config-changed`,
  `juju-info-relation-joined` or `juju-info-relation-changed` fires, the charm
  shall reconcile the on-disk configuration from current Juju state.
  *(`src/charm.py:109-120`)*
- **REQ-2** — The charm shall not observe `juju-info-relation-broken`, because the
  subordinate unit is being torn down at that point.
  *(`src/charm.py:114-117`, comment)*
- **REQ-3** — The charm shall perform its work on every unit regardless of
  leadership; no handler consults `unit.is_leader()`. *(`src/charm.py:104-185`)*
- **REQ-4** — While reconciling, the charm shall report `MaintenanceStatus`
  ("Applying prompt configuration"), and on success shall report `ActiveStatus`
  naming the label, the colour, the configured shells and — where the principal
  is known — the principal unit. *(`src/charm.py:131,141-147`)*
- **REQ-5** — The charm shall reconcile idempotently: repeating a hook with
  unchanged inputs shall leave both profiles byte-identical and produce exactly
  one managed block. *(`src/charm.py:228`; asserted at `tests/unit/test_charm.py:97-105`)*

### 7.2 Configuration validation

- **REQ-6** — The charm shall strip surrounding whitespace from
  `environment-type` and shall strip and lowercase `prompt-color` before use.
  *(`src/charm.py:83,90`)*
- **REQ-7** — When `environment-type` does not match the label pattern, the charm
  shall enter `BlockedStatus` with a message naming the option and the expected
  character set. *(`src/charm.py:84-88`)*
- **REQ-8** — When `prompt-color` is not one of the seven supported colours, the
  charm shall enter `BlockedStatus` with a message listing the valid colours.
  *(`src/charm.py:90-95`)*
- **REQ-9** — While the configuration is invalid, the charm shall write nothing
  to disk and shall leave any previously applied configuration untouched — the
  validation happens before the first write and returns early.
  *(`src/charm.py:125-129`; asserted at `tests/unit/test_charm.py:146-152`)*

### 7.3 File management

- **REQ-10** — The charm shall write `/usr/local/bin/juju_dynamic_prompt.py` with
  mode `0755` and the shell profiles with mode `0644`.
  *(`src/charm.py:133,229`)*
- **REQ-11** — The charm shall write every file atomically, via a sibling
  `.<name>.juju-tmp` file that is chmod'ed and then `replace`d, so a shell can
  never read a half-written script. *(`src/charm.py:188-194`)*
- **REQ-12** — The charm shall create missing parent directories for any file it
  writes. *(`src/charm.py:190`)*
- **REQ-13** — When a shell profile already exists, the charm shall preserve all
  content outside its managed markers and append the block at the end, inserting
  a newline first if the existing body does not end with one.
  *(`src/charm.py:220-226`; asserted at `tests/unit/test_charm.py:86-94`)*
- **REQ-14** — When a managed block is already present, the charm shall replace it
  in place rather than append a second copy. *(`src/charm.py:197-208,221`)*
- **REQ-15** — Where `enable-zsh` is `true`, the charm shall install the managed
  block into `/etc/zsh/zshrc`, creating the file if absent.
  *(`src/charm.py:135,214-218`)*
- **REQ-16** — While `enable-zsh` is `false`, the charm shall remove the managed
  block from `/etc/zsh/zshrc` if the file exists, and shall not create the file
  if it does not. *(`src/charm.py:212-214`; asserted at `tests/unit/test_charm.py:119-122`)*
- **REQ-17** — When applying the configuration raises `OSError`, the charm shall
  log the traceback and enter `BlockedStatus` with the message
  `Failed to apply prompt: <error>`. *(`src/charm.py:136-139`)*
- **REQ-18** — When the file content would be unchanged, the charm shall skip the
  write entirely. *(`src/charm.py:228`)*

### 7.4 Removal

- **REQ-19** — When `remove` fires, the charm shall strip its managed block from
  both profiles and unlink the prompt script, leaving no trace outside its own
  directory. *(`src/charm.py:149-158`; asserted at `tests/unit/test_charm.py:125-133`)*
- **REQ-20** — When removal raises `OSError`, the charm shall log the failure and
  continue, so that teardown is never blocked. *(`src/charm.py:156-158`)*
- **REQ-21** — The charm shall tolerate a missing prompt script during removal
  (`unlink(missing_ok=True)`). *(`src/charm.py:155`)*

### 7.5 Principal-unit discovery

- **REQ-22** — The charm shall take the principal unit name from the single
  remote unit of the container-scoped `juju-info` relation.
  *(`src/charm.py:160-169`)*
- **REQ-23** — While no principal unit is known — for example during `install`,
  before the relation has joined — the charm shall render the script with an
  empty `PRINCIPAL_UNIT` and shall omit the principal from the `ActiveStatus`
  message. *(`src/charm.py:143,184`; asserted at `tests/unit/test_charm.py:188-201`)*

### 7.6 Prompt rendering (generated script)

- **REQ-24** — The script shall print, on stdout, the segments
  `[LABEL]`, Juju model, principal unit and hostname joined by `" - "`, followed
  by `<user>:<cwd>$ `. *(`templates/prompt.py.j2:85-98`)*
- **REQ-25** — The script shall upper-case the environment label.
  *(`templates/prompt.py.j2:86`)*
- **REQ-26** — When a segment is empty, the script shall omit it rather than emit
  a blank ` -  - ` gap. *(`templates/prompt.py.j2:91`; asserted at `tests/unit/test_charm.py:213-219`)*
- **REQ-27** — When invoked as `… bash`, the script shall wrap non-printing escape
  sequences in `\[`/`\]`; when invoked as `… zsh`, in `%{`/`%}`; for any other
  argument it shall emit no delimiters. *(`templates/prompt.py.j2:32-35,81`)*
- **REQ-28** — The script shall escape shell-significant characters in every
  interpolated segment: backslashes for Bash, `%` for Zsh.
  *(`templates/prompt.py.j2:38-44`; asserted at `tests/unit/test_charm.py:242-251`)*
- **REQ-29** — When `PROMPT_COLOR` is not a known colour, the script shall fall
  back to green. *(`templates/prompt.py.j2:82`)*
- **REQ-30** — The script shall resolve the user from `USER`, `LOGNAME` or
  `USERNAME` in that order, falling back to the literal `user`.
  *(`templates/prompt.py.j2:47-52`)*
- **REQ-31** — The script shall read the hostname at prompt time from
  `os.uname().nodename`, falling back to `unknown-host` on `OSError` or an empty
  nodename. *(`templates/prompt.py.j2:55-64`)*
- **REQ-32** — The script shall abbreviate the working directory to `~` when it is
  the home directory or `~/…` when beneath it, and shall print `?` when the
  working directory cannot be read. *(`templates/prompt.py.j2:67-77`)*
- **REQ-33** — When no shell argument is given, the script shall default to
  `bash`. *(`templates/prompt.py.j2:102`)*
- **REQ-34** — The script shall reset the colour at the end of the prompt so that
  typed input is not coloured. *(`templates/prompt.py.j2:27,95-97`)*

### 7.7 Shell integration

- **REQ-35** — While a Bash shell is non-interactive, the injected snippet shall
  `return` immediately without defining the prompt hook.
  *(`src/charm.py:42-45`)*
- **REQ-36** — When `PROMPT_COMMAND` already contains the hook, the snippet shall
  not add it again; when empty it shall set it; otherwise it shall prepend the
  hook before the operator's existing commands. *(`src/charm.py:49-53`)*
- **REQ-37** — In Zsh, the snippet shall register the hook via
  `add-zsh-hook precmd`, which is itself idempotent per function name.
  *(`src/charm.py:63-64`)*
- **REQ-38** — The prompt shall apply only to shells started after the change;
  existing sessions keep the prompt they were started with. *(inherent to
  profile-based installation; stated in `README.md`)*

## 8. Non-functional observations

**Security.** Configuration reaches the unit as *data*, never as shell text.
`environment-type` is constrained by a regex that excludes quotes, `$`,
backticks, newlines and `;` (`src/charm.py:36`), and is then embedded through
Jinja's `tojson` filter as a Python string literal
(`templates/prompt.py.j2:13-16`) — two independent barriers against injection
into the generated script. The test suite exercises the newline case explicitly
(`tests/unit/test_charm.py:142`). The charm shells out to nothing: there is no
`subprocess`, no `os.system`, no package installation and no network access in
`src/charm.py`.

**Blast radius.** Writes are confined to three fixed absolute paths
(`src/charm.py:28-30`) and, within the two operator-owned profiles, to the
marked region. There is no user-supplied path anywhere.

**Idempotency and convergence.** `_apply_block` is a strip-then-append
reconciliation rather than an append, and `_write_file` is skipped when content
is unchanged (`src/charm.py:228`), so repeated hooks cause neither duplication
nor mtime churn.

**Failure handling.** Two distinct policies, both deliberate: apply-time errors
block the unit and surface the message (REQ-17); remove-time errors are logged
and swallowed so teardown proceeds (REQ-20).

**Performance.** The prompt costs one Python interpreter start-up per command in
every interactive shell (`src/charm.py:47,61`). That is a few tens of
milliseconds of latency before each prompt — acceptable for an operator shell,
but it is the design's main runtime cost.

**Permissions.** All writes assume root, which is what Juju machine hooks run
as. Profiles are normalised to `0644` and the script to `0755` on every write
(`src/charm.py:133,229`), so local permission edits do not survive a reconcile.

**Portability.** The prompt script targets the unit's system `python3` and uses
only the standard library (`os`, `sys`), so it is independent of the charm's
vendored virtualenv. `%`-formatting and f-strings are avoided in the template's
runtime code paths, keeping it valid for any Python 3.

**Observability.** Status messages carry the whole applied state (label, colour,
shells, principal), so `juju status` alone tells an operator what a unit is
doing. Failures additionally go to the unit log via `logger.exception`.

## 9. Acceptance criteria

Derived from the 15 tests in `tests/unit/test_charm.py`, which combine Scenario
state transitions with *executing* the generated script in a subprocess and
comparing real output (`tests/unit/test_charm.py:168-176`).

| # | Criterion | Test |
| --- | --- | --- |
| AC-1 | `config-changed` writes a `0755` script containing the label and colour, and marked blocks referencing it in both profiles | `test_config_changed_writes_script_and_profiles` |
| AC-2 | `install` alone produces a working configuration and `ActiveStatus` | `test_install_applies_configuration` |
| AC-3 | Pre-existing profile content survives verbatim, at the top of the file | `test_existing_profile_content_is_preserved` |
| AC-4 | Re-applying leaves exactly one block and updates the script | `test_reapplying_does_not_duplicate_the_block` |
| AC-5 | `enable-zsh=false` removes the Zsh block only, and the status says "for bash" | `test_disabling_zsh_removes_only_the_zsh_block` |
| AC-6 | `enable-zsh=false` never creates `/etc/zsh/zshrc` | `test_zsh_profile_is_not_created_when_disabled` |
| AC-7 | `remove` deletes the script and restores both profiles | `test_remove_undoes_every_change` |
| AC-8 | Five invalid-config cases block the unit and touch no file | `test_invalid_config_blocks_without_touching_the_disk` |
| AC-9 | Whitespace and case in config values are normalised | `test_config_values_are_normalised` |
| AC-10 | Model name and principal unit are baked into the script and named in the status | `test_script_records_model_and_principal_unit` |
| AC-11 | `relation-joined` back-fills a principal that was unknown at `install` | `test_relation_joined_refreshes_the_principal_unit` |
| AC-12 | The executed prompt equals `[PRODUCTION] - staging-mdl - ubuntu/3 - <host> ubuntu:<cwd>$ ` | `test_prompt_shows_env_model_unit_and_hostname` |
| AC-13 | An unknown principal yields no double separator | `test_unknown_segments_are_omitted_not_blank` |
| AC-14 | Escapes are wrapped in `\[…\]` for Bash and `%{…%}` for Zsh | `test_generated_script_wraps_escapes_for_{bash,zsh}` |
| AC-15 | A directory named `100%_back\slash` is escaped per shell | `test_generated_script_escapes_prompt_metacharacters` |

**Not covered:** integration/functional tests against a real Juju model, `start`
and `upgrade-charm` handlers (they share `_reconcile`, but no test drives them),
the `OSError` branches (REQ-17, REQ-20), and the actual sourcing of the snippets
by a real `bash`/`zsh`.

## 10. Uncertainties and discrepancies

1. **The documented label pattern is wider than the implemented one.**
   `charmcraft.yaml:40-42` and `README.md` both say "1-32 characters from
   `[A-Za-z0-9 _.:@+-]`", but `ENV_LABEL` (`src/charm.py:36`) additionally
   requires the *first* character to be `[A-Za-z0-9_]`. `.staging` or `-prod`
   are rejected with a message that says they should be accepted. Either the
   regex or the description should move.
2. **`enable-zsh=true` creates `/etc/zsh/zshrc` even when Zsh is not installed**
   (`src/charm.py:214-218`, `190`). Since `enable-zsh` defaults to `true`, the
   default deployment creates `/etc/zsh/` on machines with no Zsh. If Zsh is
   installed afterwards, `dpkg` will see a pre-existing conffile it did not
   create and prompt on upgrade. Intentional convenience or an oversight is not
   determinable from the code.
3. **An unterminated `BLOCK_START` swallows the rest of the file.**
   `_strip_block` (`src/charm.py:197-208`) skips lines from `BLOCK_START` until
   it finds `BLOCK_END`; if the end marker has been deleted by hand, every
   subsequent line is dropped and then overwritten. There is no guard and no
   test for a mangled block.
4. **Profile mode and ownership are normalised on every write.** `_write_file`
   creates a fresh file and `replace`s the original (`src/charm.py:188-194`), so
   a profile with non-default permissions, extended attributes or a hard link is
   silently reset to `0644` on the next reconcile.
5. **A removed charm leaves live shells with an empty prompt.** After `remove`,
   sessions still running the hook call a script that no longer exists;
   `PS1="$(…)"` then evaluates to the empty string until the shell restarts.
   Not observed in tests; inferred from `src/charm.py:47` plus REQ-19.
6. **`amd64`-only.** `charmcraft.yaml:24-25` declares a single platform even
   though nothing in the charm is architecture-specific. Whether arm64 was
   deliberately excluded is unclear.
7. **No `update-status` handler**, so a unit whose profiles were edited by hand
   between hooks will keep reporting `ActiveStatus` until the next event.
8. **No `enable-bash` option**: Bash is unconditional (`src/charm.py:134`). This
   is consistent with the charm's purpose but is an asymmetry with `enable-zsh`.

## 11. Recommendations

Ordered by value, and each is a small change:

1. **Reconcile the label pattern with its documentation** (uncertainty 1) — fix
   the description in `charmcraft.yaml` and `README.md`, or relax the regex's
   first character. One-line change either way; today the error message
   contradicts the docs.
2. **Guard `_strip_block` against a missing end marker** (uncertainty 3) — if
   `skipping` is still true at the end of the loop, log a warning and keep the
   original lines rather than dropping them. Add a test for a hand-mangled block.
3. **Only create `/etc/zsh/zshrc` when Zsh is present** (uncertainty 2) — e.g.
   skip creation when the directory does not already exist, and let the existing
   `enable-zsh` re-run pick it up later. Alternatively document the behaviour.
4. **Cover the `OSError` paths** — a test that makes `_write_file` raise would
   pin REQ-17 and REQ-20, which are currently untested error policy.
5. **Consider caching in the Bash hook** — the prompt spawns Python per command
   (§8, Performance). The baked-in segments could be exported once and only the
   hostname/cwd computed by the shell itself, if the latency ever matters.
6. **Add an integration test** (`tox -e integration` with `jubilant` or
   `pytest-operator`) that deploys against `ubuntu` and asserts the block lands
   in a real `/etc/bash.bashrc`, closing the gap listed at the end of §9.
