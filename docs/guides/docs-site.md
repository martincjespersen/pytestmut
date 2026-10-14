# Reports in your docs site

The HTML report is a single self-contained file, so it can live anywhere you
can host a page. This guide adds it to an MkDocs site as a page, the way the
[pytestmut docs do](../mutation.md).

## 1. Add the hook

Copy this file into your project, for example as
`docs/hooks/mutation_report.py`:

```python title="docs/hooks/mutation_report.py"
"""MkDocs hook: publish the latest mutation testing report with the docs."""

from pathlib import Path

from mkdocs.structure.files import File

from pytestmut.config.loader import load_config
from pytestmut.exceptions import PytestmutError

SITE_PATH = "mutation-report/index.html"
PLACEHOLDER = "<p>No mutation report yet. Run <code>pytestmut run --report html</code>.</p>"


def _report_path(config):
    project = Path(config.config_file_path).resolve().parent
    try:
        settings = load_config(project)
    except PytestmutError:
        return None
    return settings.root / settings.report.output_dir / "report.html"


def on_files(files, config):
    report = _report_path(config)
    if report is not None and report.is_file():
        files.append(File.generated(config, SITE_PATH, abs_src_path=str(report)))
    else:
        files.append(File.generated(config, SITE_PATH, content=PLACEHOLDER))
    return files


def on_serve(server, config, builder):
    report = _report_path(config)
    if report is not None and report.parent.is_dir():
        server.watch(str(report.parent))   # reload when a new report is written
    return server
```

It needs MkDocs 1.6 or newer and pytestmut installed in the docs environment.
It reads the report's location from your `[tool.pytestmut.report]` settings.

## 2. Register it

```yaml title="mkdocs.yml"
hooks:
  - docs/hooks/mutation_report.py
exclude_docs: |
  hooks/
nav:
  - Home: index.md
  - Mutation report: mutation.md
```

## 3. Add the page

```markdown title="docs/mutation.md"
# Mutation report

<iframe id="pytestmut-report-frame" src="../mutation-report/"
        title="Mutation testing report"
        style="width: 100%; min-height: 80vh; border: 0;"></iframe>

<script>
(function () {
  var frame = document.getElementById("pytestmut-report-frame");
  function fit() {
    try {
      var root = frame.contentDocument && frame.contentDocument.documentElement;
      if (root) { frame.style.height = root.scrollHeight + "px"; }
    } catch (error) { return; }
  }
  frame.addEventListener("load", function () {
    fit();
    if (window.ResizeObserver && frame.contentDocument) {
      new ResizeObserver(fit).observe(frame.contentDocument.body);
    }
  });
})();
</script>
```

The script grows the frame to the report's height, so the page scrolls as
one.

!!! warning "Keep the frame's id unique"
    MkDocs gives every heading an id: `# Mutation report` becomes
    `mutation-report`. Give the frame any other id, or the script will find
    the heading instead.

## 4. Build with a fresh report

```bash
pytestmut run --report text --report html   # exit code 1 still writes a report
mkdocs serve
```

In a nox session, let a missed threshold still produce docs:

```python title="noxfile.py"
@nox.session(name="Serve Docs")
def serve_docs(session):
    session.run("pytestmut", "run", "--report", "text", "--report", "html",
                success_codes=[0, 1])
    session.run("mkdocs", "serve")
```

While `mkdocs serve` runs, writing a new report reloads the page.
