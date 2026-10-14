# CI/CD

Fail a pipeline when tests get weaker, show the result on the pull request,
and keep the full report as an artifact.

## GitHub Actions

```yaml title=".github/workflows/mutation.yml"
name: Mutation testing

on:
  pull_request:
  push:
    branches: [main]

jobs:
  mutation:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0                   # needed for --diff on pull requests

      - uses: astral-sh/setup-uv@v4

      - run: uv sync

      - name: Restore verdict cache
        uses: actions/cache@v4
        with:
          path: .pytestmut/cache
          key: pytestmut-${{ runner.os }}-${{ hashFiles('uv.lock') }}-${{ github.sha }}
          restore-keys: pytestmut-${{ runner.os }}-${{ hashFiles('uv.lock') }}-

      - name: Mutation testing (changed lines on pull requests)
        if: github.event_name == 'pull_request'
        run: uv run pytestmut run --diff origin/${{ github.base_ref }} --report text --report markdown --report html

      - name: Mutation testing (everything on main)
        if: github.event_name == 'push'
        run: uv run pytestmut run --report text --report markdown --report html

      - name: Job summary
        if: always()
        run: cat .pytestmut/reports/report.md >> "$GITHUB_STEP_SUMMARY" || true

      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: mutation-report
          path: .pytestmut/reports/
```

**What this gives you:**

- **Pull requests test only what they change**, so they stay fast. Pushes to
  `main` run everything.
- **The step fails** when a policy misses its thresholds (exit code 1). It
  also fails on setup problems (exit codes 2 to 5).
- **The markdown report becomes the job summary**, including the guidance for
  every survivor.
- **The HTML report is downloadable** from the run.
- **The cache carries verdicts between runs.** It is safe to reuse: kills are
  confirmed again, and other verdicts are reused only when nothing they depend
  on has changed.

## Other CI systems

The same three ingredients work anywhere:

```bash
# 1. run; the exit code is the gate
pytestmut run --report text --report markdown --report html

# 2. publish .pytestmut/reports/report.md and report.html

# 3. cache .pytestmut/cache between runs
```

=== "GitLab CI"

    ```yaml
    mutation:
      script:
        - uv sync
        - uv run pytestmut run --report text --report html
      cache:
        key: pytestmut-$CI_COMMIT_REF_SLUG
        paths: [.pytestmut/cache]
      artifacts:
        when: always
        paths: [.pytestmut/reports/]
    ```

=== "Azure Pipelines"

    ```yaml
    - script: uv sync && uv run pytestmut run --report text --report html
      displayName: Mutation testing
    - task: PublishPipelineArtifact@1
      condition: always()
      inputs:
        targetPath: .pytestmut/reports
        artifact: mutation-report
    ```

## Gates for CI

Decide what fails the build in the configuration, not in the pipeline:

```toml
[tool.pytestmut.thresholds]
min_score = 80.0

[[tool.pytestmut.policy]]
name = "core"
paths = ["src/app/core/**"]
min_score = 95.0
max_survivors = 0
```

Override once, for an experiment:
`pytestmut run --min-score 90`.

## Keeping CI runners healthy

- **Workers** default to the CPUs the runner allows, container limits
  included.
- **Memory** stays within half the runner's RAM by default. On small runners,
  set it explicitly:
  ```bash
  pytestmut run --max-memory 3GiB
  ```
- **Credential variables** (`*_TOKEN`, `AWS_*`, `DATABASE_URL`, ...) are hidden
  from the test processes. If your tests need one, allow it:
  ```toml
  [tool.pytestmut.safety]
  env_keep = ["TEST_DATABASE_URL"]
  ```
- **Long suites.** Set a budget per mutant with `timeout.max_seconds`, and use
  `--diff` on pull requests.
