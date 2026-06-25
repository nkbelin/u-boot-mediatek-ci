# u-boot-mediatek-ci

CI helpers for the MediaTek U-Boot tree. The build gate compiles **every commit
of a pushed series** with `buildman`, for the MediaTek defconfigs listed in
`boards/mediatek.txt`, so a series can't break compilation or `git bisect`. The
same script runs locally and in GitLab CI.

## Layout
```
boards/mediatek.txt        board scope: globs over configs/*_defconfig
                           (or an "@select: <buildman args>" line)
scripts/build_commits.py   the gate: resolve boards + commit range, run buildman
ci/gitlab-ci-mediatek.yml  the GitLab CI job (deploy into the branch under test)
```

## Run locally
Run inside the same pinned CI image so the toolchain matches CI:
```sh
# from your U-Boot checkout, on the branch under test:
docker run --rm -v "$PWD":/work -w /work \
  trini/u-boot-gitlab-ci-runner:noble-20251013-23Jan2026 \
  bash -lc 'git config --global --add safe.directory /work &&
            python3 /path/to/u-boot-mediatek-ci/scripts/build_commits.py'
```
The runner image provides the cross-toolchain and `buildman`. On a bare host
you instead need an aarch64 toolchain plus U-Boot's build dependencies.

### Useful environment variables
- `MTK_COUNT`   build the last N commits (e.g. `MTK_COUNT=5` for your top 5).
- `WERROR=1`    treat build warnings as errors.
- `BOARDS_FILE` alternate board list.
- `OUT_DIR`     buildman output dir (default `$TMPDIR/mtk-build`).
- `--dry-run`   resolve boards/range and print the buildman command, don't build.

## In CI
`ci/gitlab-ci-mediatek.yml` clones this repo, runs `build_commits.py`, and saves
the buildman summary as the `build-summary.txt` artifact. Add it to the branch
under test as `.gitlab-ci-mediatek.yml`, set it as the project's CI/CD
configuration file, set the job's `tags:` to an available runner (or remove it
for an untagged runner), and point `MTK_CI_REPO` at where this repo is hosted.

### Commit range
- Fast-forward push: builds exactly the pushed commits (`CI_COMMIT_BEFORE_SHA..HEAD`).
- New branch / force-push: builds the last `MTK_FALLBACK_COUNT` commits.
- `MTK_COUNT` overrides both.

## Board scope
The MediaTek Genio family by default (`mt8365_evk` + `*genio*`). Edit
`boards/mediatek.txt` to widen it; use `@select: mediatek` to build all MediaTek
boards (incl. the MT76xx routers).
