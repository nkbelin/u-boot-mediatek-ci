#!/usr/bin/env python3
"""MediaTek U-Boot build gate: build every commit of a pushed series.

One entry point for local runs and GitLab CI. It:
  1. resolves the board set from boards/mediatek.txt (globs, or an @select line),
  2. determines the commit range (the commits pushed in this series),
  3. runs buildman across that range for those boards (allow-missing blobs),
  4. writes a human-readable summary artifact,
and exits non-zero if any commit fails to build (with WERROR=1, on warnings too).

The environment is assumed to be the pinned CI image (buildman, its python deps
and the cross-toolchain are already present); the same image is used for local
runs, so there is no venv/toolchain bootstrap here.

Configuration is via environment variables, so the CI job and a local shell use
the exact same code path:
  UBOOT_SRC     U-Boot tree to build (default: git toplevel of CWD)
  BOARDS_FILE   board list (default: ../boards/mediatek.txt next to this script)
  MTK_COUNT          build the last N commits (manual override)
  MTK_FALLBACK_COUNT commits to build when the pushed range is unknown
                     (new branch / force-push / manual run); default 50
  CI_COMMIT_BEFORE_SHA  set by GitLab on push; used for the fast-forward range
  WERROR=1      treat warnings as errors (buildman -E)
  OUT_DIR       buildman output dir (default: $TMPDIR/mtk-build)
  SUMMARY_FILE  where to write the summary (default: <UBOOT_SRC>/build-summary.txt)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from glob import glob
from pathlib import Path

DEFCONFIG_SUFFIX = "_defconfig"


def log(msg: str) -> None:
    print(f">> {msg}", file=sys.stderr, flush=True)


def die(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def git(src, *args, check=True):
    """Run git in `src`, capturing output. Dies on failure when check=True."""
    cp = subprocess.run(["git", "-C", str(src), *args], text=True, capture_output=True)
    if check and cp.returncode != 0:
        die(f"git {' '.join(args)} failed: {cp.stderr.strip()}")
    return cp


def buildman(src, *args, stream=False):
    """Invoke the tree's buildman. stream=True inherits stdout/stderr (live log)."""
    cmd = [sys.executable, "tools/buildman/buildman", *args]
    if stream:
        return subprocess.run(cmd, cwd=str(src))
    return subprocess.run(cmd, cwd=str(src), capture_output=True, text=True)


def resolve_boards(src: Path, boards_file: Path):
    """Return (board_names, select). Exactly one is meaningful:
       - select: a passthrough buildman selector (from an "@select: ..." line)
       - board_names: list from glob/exact patterns matched against configs/.
    """
    names: list[str] = []
    for raw in boards_file.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("@select:"):
            return [], line[len("@select:"):].strip()
        matched = False
        for f in sorted(glob(str(src / "configs" / f"{line}{DEFCONFIG_SUFFIX}"))):
            names.append(Path(f).name[: -len(DEFCONFIG_SUFFIX)])
            matched = True
        if not matched:
            log(f"warning: pattern '{line}' matched no defconfig")
    seen, uniq = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq, ""


def resolve_range(src: Path):
    """Return (n_commits, range_desc). n=0 means nothing to build.

    The submission under review is always the top of the branch, so:
      1. MTK_COUNT set                 -> build the last N commits (manual override)
      2. fast-forward push             -> CI_COMMIT_BEFORE_SHA..HEAD (the series)
      3. new branch / force-push /     -> the last MTK_FALLBACK_COUNT commits
         rebase / manual run
    No merge-base with upstream master: that would rebuild the whole un-upstreamed
    stack (hundreds of commits) on every push.
    """
    count = os.environ.get("MTK_COUNT")
    if count:
        n = int(count)
        log(f"explicit count: building last {n} commit(s)")
        return n, f"last {n}"

    before = os.environ.get("CI_COMMIT_BEFORE_SHA", "")
    if before and set(before) != {"0"}:  # not the all-zero "new branch" sentinel
        # Use BEFORE..HEAD only on a true fast-forward (BEFORE is an ancestor).
        if git(src, "merge-base", "--is-ancestor", before, "HEAD", check=False).returncode == 0:
            n = int(git(src, "rev-list", "--count", f"{before}..HEAD").stdout.strip())
            log(f"fast-forward push: building {n} new commit(s) since {before[:12]}")
            return n, f"{before[:12]}..HEAD"
        log(f"push is not a fast-forward of {before[:12]} (force-push/rebase); using fallback")

    fallback = int(os.environ.get("MTK_FALLBACK_COUNT", "50"))
    avail = int(git(src, "rev-list", "--count", "HEAD").stdout.strip())
    n = min(fallback, avail)
    log(f"fallback: building last {n} commit(s) (cap {fallback})")
    return n, f"last {n}"


def main() -> int:
    ap = argparse.ArgumentParser(description="MediaTek U-Boot build gate")
    ap.add_argument("--dry-run", action="store_true",
                    default=os.environ.get("MTK_DRYRUN") == "1",
                    help="resolve boards/range and print the buildman command, "
                         "but do not build")
    args = ap.parse_args()

    src = Path(os.environ.get("UBOOT_SRC")
               or git(Path.cwd(), "rev-parse", "--show-toplevel").stdout.strip())
    if not (src / "tools/buildman/buildman").exists():
        die(f"not a U-Boot tree (no tools/buildman/buildman): {src}")

    scripts_dir = Path(__file__).resolve().parent
    boards_file = Path(os.environ.get("BOARDS_FILE",
                                      scripts_dir.parent / "boards" / "mediatek.txt"))
    if not boards_file.exists():
        die(f"board list not found: {boards_file}")
    out_dir = os.environ.get("OUT_DIR",
                             str(Path(os.environ.get("TMPDIR", "/tmp")) / "mtk-build"))
    summary_file = os.environ.get("SUMMARY_FILE", str(src / "build-summary.txt"))
    werror = os.environ.get("WERROR", "0") == "1"

    names, select = resolve_boards(src, boards_file)
    n, rng = resolve_range(src)

    if n == 0:
        log("no commits above the upstream base -- nothing to build.")
        return 0

    if select:
        sel = select.split()
        log(f"board selection (buildman): {select}")
    elif names:
        sel = ["--boards", ",".join(names)]
        log(f"boards: {','.join(names)}")
    else:
        die(f"no boards resolved from {boards_file}")

    flags = ["-o", out_dir, "-c", str(n), "-M", *sel]
    if werror:
        flags.append("-E")
        log("warnings-as-errors: ON (-E)")
    else:
        log("warnings-as-errors: off (errors only)")

    if args.dry_run:
        print("DRY RUN -- would run:")
        print("  buildman " + " ".join(flags))
        print(f"  (in {src}, range {rng}, {n} commit(s))")
        return 0

    log(f"building {n} commit(s) [{rng}] into {out_dir}")
    ret = buildman(src, *flags, stream=True).returncode

    # Human-readable summary artifact (reuses the build dir, no recompile).
    summ = buildman(src, "-o", out_dir, "-c", str(n), *sel, "-se")
    Path(summary_file).write_text(summ.stdout)
    sys.stdout.write(summ.stdout)

    if ret == 0:
        log(f"OK: all {n} commit(s) built clean for the selected boards")
    else:
        log(f"FAILED (buildman exit {ret}) -- see {summary_file}")
    return ret


if __name__ == "__main__":
    sys.exit(main())
