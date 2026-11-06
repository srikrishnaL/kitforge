# Releasing

One-time setup (repository owner):

1. Create a PyPI API token at https://pypi.org/manage/account/token/ with scope "Entire account"
   (new projects can't be scoped until their first upload).
2. In GitHub: Settings → Secrets and variables → Actions → New repository secret,
   name `PYPI_API_TOKEN`, value the token.
3. For Node packages later: create an npm automation token and save it as `NPM_TOKEN`.

Per release:

1. Bump `version` in the package's `pyproject.toml` or `package.json` and add a CHANGELOG entry.
2. Merge to `main`.
3. Tag and push: `git tag mcptest-v0.1.0 && git push origin mcptest-v0.1.0`.

The `Release` workflow refuses to publish if the tag and the package version differ or the tests
fail, and it creates a GitHub release with the built files.
