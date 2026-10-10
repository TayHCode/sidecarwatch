# Contributing

Python 3.11 or newer; runtime and tests use the standard library only. Run one suite process at a time, in a scratch home with fake CLIs first on PATH:

```sh
mkdir -p .scratch/home
env -i HOME="$PWD/.scratch/home" LANG=C.UTF-8 \
  PATH="$PWD/tests/fixtures/bin:/usr/bin:/bin" \
  python3 -m unittest discover -s tests -q
```

Repeat with an absolute path to each supported Python interpreter. The suite blocks network access and refuses real agent CLI executables. Synthetic fixtures only: never copy a real transcript, project profile, state folder or private term list into this repository. Build key-shaped test values at run time from pieces.

For a format change, collect only `sidecarwatch doctor --shapes` counts. Add a small synthetic record sequence to the appropriate parser test. Observation support and automatic eligibility are separate: update [the supported grammar](docs/event-shapes.md) and test unknown fields, ordering, failures, missing outcomes and pending tools before adding eligibility. No quiet-time or prose fallback may authorize an automatic reply. Keep every no-send regression.

The 24 cases in `tests/characterization/cases.py` document the original behavior and each intentional port difference. The final suite checks the public CLI adapter; the historical original adapter is preserved only in Git history.

Run the terms test with an external nonempty list. An unset list skips it; missing, empty or repository-local lists fail. JSON terms/categories or one term per line are accepted. Output is counts and file:line locations only.

```sh
env -i HOME="$PWD/.scratch/home" LANG=C.UTF-8 \
  PATH="$PWD/tests/fixtures/bin:/usr/bin:/bin" \
  FORBIDDEN_TERMS_FILE=/path/outside/repo/terms.txt \
  python3 -m unittest tests.test_forbidden_terms -v
```

Generic-content tests also check tracked paths and text. Stage explicit intended paths before that check so new files are included. The terms test checks the current tracked tree and commit metadata; a separate export review must also check all historical blobs. Do not submit raw scanner matches or private logs. Use the issue templates for synthetic reproductions.
