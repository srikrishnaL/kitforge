# Releasing

One-time setup (repository owner):

1. Create a PyPI API token at https://pypi.org/manage/account/token/ with scope "Entire account"
   (new projects can't be scoped until their first upload).
2. In GitHub: Settings → Secrets and variables → Actions → New repository secret,
   name `PYPI_API_TOKEN`, value the token.
3. For Node packages later: create an npm automation token and save it as `NPM_TOKEN`.

Per release:

1. Bump `version` in the package's `pyproject.toml` or `package.json` and add a CHANGELOG entry.
2. Merge to `main`. That's it.

On every push to `main` the `Release` workflow lists each package's published name and version
and releases those whose tag `<published name>-v<version>` doesn't exist yet: it checks the
version, runs the tests, builds, publishes to PyPI or npm, then creates the tag and a GitHub
release with the built files. Packages whose version didn't change are skipped, so merges that
don't bump a version publish nothing.

Other ways to start it:

- Push or create (as a GitHub release) a tag `<published name>-v<version>` or
  `<folder>-v<version>`; the version must match the package.
- Actions → Release → Run workflow: releases whatever is missing, e.g. after fixing a failed run.

A re-run after a partial failure is safe: a version already on PyPI or npm is not uploaded again.
