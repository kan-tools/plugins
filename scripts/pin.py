#!/usr/bin/env python3
"""Derive every git `sha` in marketplace.json from its `ref`, or check that it matches.

Why this exists. kan-tools/day#157 recorded three ways a hand-written
marketplace entry goes wrong, and one of them is invisible on inspection:

    git ls-remote --tags <url> v1.2.3

prints the **tag object** for an annotated tag, not the commit it points at.
day's tags are annotated and kan's are not, so copying that column pins day at a
non-commit and kan correctly, from the same command, with no visible difference
between the two lines. A sha is forty hex characters either way.

So the sha is derived here rather than pasted. `--check` re-derives it in CI, and
reports TAG-OBJECT as its own outcome instead of folding it into a generic
mismatch, because that is the failure that does not announce itself.

Outcomes, never conflated:

    OK           the recorded sha is the commit that `ref` resolves to
    DRIFTED      the recorded sha is some other commit
    TAG-OBJECT   the recorded sha is the annotated tag object for `ref`
    UNPINNED     the entry names a ref but records no sha
    NO-SUCH-REF  the remote answered and does not have `ref`
    UNREACHABLE  the remote could not be read at all

NO-SUCH-REF is kept apart from UNREACHABLE, and is a problem rather than a
could-not-check, because the remote *answered*: a tag that is not there is a
known-wrong pin (a typo, or a release that was never pushed), not a missing
answer. Folding it into UNREACHABLE would let a permanently broken entry report
as a transient network fault forever.

Exit codes are the contract:

    0  every git-sourced entry is OK
    1  at least one entry is DRIFTED, TAG-OBJECT, UNPINNED, or NO-SUCH-REF
    2  at least one entry was UNREACHABLE -- the check did not complete

2 outranks 1 deliberately. A caller that cannot tell "the network was down" from
"a pin is wrong" will eventually read the first as the second; day's own history
of a mutation harness reporting SURVIVED for a build error is what that costs.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

MANIFEST = pathlib.Path(__file__).resolve().parent.parent / ".claude-plugin" / "marketplace.json"

# The source forms that clone git and therefore accept ref/sha. `npm` and
# `archive` do not, and are skipped rather than reported -- there is no pin to
# check, which is not the same as a pin that is fine.
GIT_SOURCES = {"url", "github", "git-subdir"}


def remote_url(source: dict) -> str:
    if source.get("source") == "github":
        # Deliberately HTTPS. `{"source": "github", "repo": ...}` resolves to SSH
        # in the installer, which fails with "Permission denied (publickey)" for
        # anyone without a key on the machine -- that is, most people installing
        # a plugin for the first time. This script never emits the github form,
        # but it still has to resolve one if a hand edit introduces it.
        return f"https://github.com/{source['repo']}.git"
    return source["url"]


def version_key(tag: str) -> tuple | None:
    """Sort key for a `vX.Y.Z` or `vX.Y.Z-pre` tag, or None if it is not one.

    A RELEASE SORTS ABOVE ITS OWN PRE-RELEASES, which plain string order gets
    backwards: `v0.12.0` is newer than `v0.12.0-beta.2`, but sorts before it
    lexically. Both kan-tools crates have shipped only pre-releases so far, so
    this is exactly the path that is never exercised here until the day it
    matters -- the failure shape `CLAUDE.md` records for a mechanism with two
    modes, tested in whichever mode the repo happens to be in.

    Pre-release ordering is deliberately shallow: `beta.2` after `beta.10` is
    wrong under semver, and correcting it means a component-wise comparison this
    does not need, because the tag it picks is checked by a human on a PR before
    it lands. Shallow and predictable beats subtly clever.
    """
    if not tag.startswith("v"):
        return None
    core, _, pre = tag[1:].partition("-")
    parts = core.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    # `1` for a final release, `0` for a pre-release, so a release wins a tie.
    return (tuple(int(p) for p in parts), 1 if not pre else 0, pre)


def newest_tag(url: str) -> str | None:
    """The highest `v*` tag on `url`, or None when it has none.

    Raises OSError when the remote is unreachable, keeping "no tags" and "could
    not ask" distinguishable the same way `resolve` does.
    """
    proc = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", url],
        capture_output=True,
        text=True,
        env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    if proc.returncode != 0:
        raise OSError(proc.stderr.strip() or f"git ls-remote exited {proc.returncode}")

    # `--refs` drops the `^{}` peeled lines, so each tag appears exactly once
    # here. Peeling is `resolve`'s job and stays there.
    tags = []
    for line in proc.stdout.splitlines():
        _, _, name = line.partition("\t")
        tag = name.strip().removeprefix("refs/tags/")
        key = version_key(tag)
        if key is not None:
            tags.append((key, tag))
    if not tags:
        return None
    return max(tags)[1]


def resolve(url: str, ref: str) -> tuple[str | None, str | None]:
    """Return (commit, tag_object) for `ref` on `url`.

    `tag_object` is None for a lightweight tag or a branch. Both are None when
    the ref is absent. Raises OSError when the remote itself is unreachable, so
    a missing ref and a missing network stay distinguishable.
    """
    proc = subprocess.run(
        ["git", "ls-remote", url, f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}", f"refs/heads/{ref}"],
        capture_output=True,
        text=True,
        env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    if proc.returncode != 0:
        raise OSError(proc.stderr.strip() or f"git ls-remote exited {proc.returncode}")

    lines = {}
    for line in proc.stdout.splitlines():
        sha, _, name = line.partition("\t")
        lines[name.strip()] = sha.strip()

    peeled = lines.get(f"refs/tags/{ref}^{{}}")
    plain = lines.get(f"refs/tags/{ref}")
    head = lines.get(f"refs/heads/{ref}")

    if peeled:
        # Annotated: the commit is the peeled line, `plain` is the tag object.
        return peeled, plain
    if plain:
        # Lightweight: the tag ref already points at the commit.
        return plain, None
    if head:
        return head, None
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify pins without writing (CI mode)")
    ap.add_argument(
        "--latest",
        action="store_true",
        help="first move every `ref` to the newest v* tag on its remote, then derive shas",
    )
    args = ap.parse_args()

    if args.latest and args.check:
        # These ask opposite questions -- one rewrites the manifest, the other
        # asserts it is already right -- and a combination that silently
        # honoured only one of them is how a CI job ends up green for the wrong
        # reason.
        print("--latest rewrites the manifest and --check refuses to; pick one.", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST.read_text())
    problems = 0
    unreachable = 0
    changed = False

    for entry in manifest["plugins"]:
        source = entry.get("source")
        name = entry["name"]

        if not isinstance(source, dict) or source.get("source") not in GIT_SOURCES:
            continue

        url = remote_url(source)

        if args.latest:
            try:
                newest = newest_tag(url)
            except OSError as exc:
                print(f"{name:12} UNREACHABLE  {url}: {exc}")
                unreachable += 1
                continue
            if newest is None:
                print(f"{name:12} NO-SUCH-REF  {url} publishes no v* tags to track")
                problems += 1
                continue
            if newest != source.get("ref"):
                print(f"{name:12} RETARGET     {source.get('ref')} -> {newest}")
                source["ref"] = newest
                changed = True

        ref = source.get("ref")
        if not ref:
            print(f"{name:12} UNPINNED     no `ref`, so nothing to derive a sha from")
            problems += 1
            continue

        try:
            commit, tag_object = resolve(url, ref)
        except OSError as exc:
            print(f"{name:12} UNREACHABLE  {url}: {exc}")
            unreachable += 1
            continue

        if commit is None:
            print(f"{name:12} NO-SUCH-REF  {url} has no ref {ref!r}")
            problems += 1
            continue

        recorded = source.get("sha")

        if args.check:
            if recorded == commit:
                print(f"{name:12} OK           {ref} -> {commit}")
            elif recorded is None:
                print(f"{name:12} UNPINNED     {ref} resolves to {commit}, no sha recorded")
                problems += 1
            elif tag_object and recorded == tag_object:
                print(
                    f"{name:12} TAG-OBJECT   {ref} is annotated; recorded {recorded} is the tag "
                    f"object, the commit is {commit}"
                )
                problems += 1
            else:
                print(f"{name:12} DRIFTED      {ref} -> {commit}, recorded {recorded}")
                problems += 1
        else:
            if recorded != commit:
                source["sha"] = commit
                changed = True
                print(f"{name:12} WROTE        {ref} -> {commit}")
            else:
                print(f"{name:12} OK           {ref} -> {commit}")

    if not args.check and changed:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")

    sys.stdout.flush()
    if unreachable:
        print(
            f"\n{unreachable} {'entry' if unreachable == 1 else 'entries'} could not be checked.",
            file=sys.stderr,
        )
        return 2
    if problems:
        print(
            f"\n{problems} {'entry needs' if problems == 1 else 'entries need'} attention.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
