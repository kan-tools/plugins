# kan-tools plugins

The plugin marketplace for the [kan-tools](https://github.com/kan-tools) projects.
This repository is the catalog only — each plugin lives in its own repo and is
fetched from there.

```console
/plugin marketplace add kan-tools/plugins
/plugin install day@kan-tools
/plugin install kan@kan-tools
```

| Plugin | What it is | Repo |
| ------ | ---------- | ---- |
| `kan`  | Local-first, provenance-preserving memory substrate for AI agents. | [kan-tools/kan](https://github.com/kan-tools/kan) |
| `day`  | Structured process layer: teloi, composable process atoms, drift assessment — all recorded in kan. | [kan-tools/day](https://github.com/kan-tools/day) |

Both plugins configure an MCP server that runs a binary of the same name, so
install the tool itself first (`cargo install kan`, `cargo install day`). day
also needs kan: `day doctor` reports the pairing and the supported range.

## Adding a release

Entries are pinned to an exact commit. To move one forward, edit its `ref` and
let the script derive the `sha`:

```console
$ python3 scripts/pin.py          # rewrites every sha from its ref
day          WROTE        v0.12.0-beta.1 -> 28599cf98e5b17317e1210897fdb2a8badea854e
```

Then commit. `scripts/pin.py --check` re-derives without writing and is what CI
runs; `claude plugin validate . --strict` checks the manifest shape.

## Three things worth knowing before editing the manifest by hand

Each of these was hit while building this catalog, and recorded in
[kan-tools/day#157](https://github.com/kan-tools/day/issues/157).

**Pins are derived, never pasted.** `git ls-remote --tags <url> <tag>` prints the
tag *object* for an annotated tag, not the commit it points at. day's tags are
annotated and kan's are not, so copying that column gives a correct pin for one
repo and a non-commit for the other, from the same command, with nothing on
screen to tell them apart. `scripts/pin.py` peels the tag; it reports
`TAG-OBJECT` as its own outcome precisely because that failure is invisible on
inspection.

**Sources are `url` with an explicit HTTPS URL, not `github`.** The shorter
`{"source": "github", "repo": "..."}` form resolves to SSH, which fails with
`Permission denied (publickey)` for anyone without a key on the machine — most
people installing a plugin for the first time. Measured on one box:
`git ls-remote` over HTTPS exits 0, over SSH exits 128. It is also what most
git-sourced entries in the official directory do.

**Sources are never relative paths.** A `"./plugin"` source copies the working
directory rather than cloning tracked files, so a built checkout drags its whole
`target/` tree along — 22 GB for day, at which point the installer gives up and
leaves an empty temp dir behind. A git source never sees untracked files. This
is invisible until someone tries it on a repo that has been built.

## Licence

MIT, matching both plugins.
