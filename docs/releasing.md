# Alpha release gates

1. Keep an integration PR with interface changes, migration notes, original finding
   IDs, remaining conditions and evidence paths. No PR merge alone closes acceptance.
2. Pass applicable Python 3.11/3.12 regression, full Ruff, changed-file formatting,
   scoped type checks, deterministic failure/recovery checks and source-dashboard
   interaction. Run the locked consumer environments independently, without training.
3. Build wheel and sdist. In clean venvs outside the checkout, install the wheel
   and a wheel rebuilt from the sdist. Exercise demo, review, dataset, verify and
   report. Bind every result to exact commit and artifact SHA-256.
4. For C/F13, collect the three actual first-user records. Keep release readiness
   pending while a required gate is unmet; don't relabel automated tests as users.
5. After gates pass, merge and repeat build/install on the exact merge commit.
   Create a same-commit version tag and GitHub **Pre-release**. Upload wheel, sdist,
   SHA256SUMS and sanitized evidence. No PyPI release is implied.
6. Reopen the public page, download every asset, compare hashes, tag, commit and
   package version. If verification fails, do not call delivery complete. Withdraw a
   defective prerelease, retain its original tag, and fix under a new version;
   never overwrite already published artifacts.

Release evidence must not include credentials, private source content, real reviewer
names or local account paths. Model quality, cross-language accuracy and training
benefits remain unverified until D supplies its own evidence.
