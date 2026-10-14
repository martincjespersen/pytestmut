# Mutation report

pytestmut tests its own core modules: the code that decides a mutant's status
and whether a run passes. This is the report of the latest run, made when these
docs were built. The thresholds it is held to are in `[tool.pytestmut]` of
`pyproject.toml`.

<iframe id="pytestmut-report-frame" src="../mutation-report/" title="Mutation testing report"
        style="width: 100%; min-height: 80vh; border: 0;"></iframe>

<script>
(function () {
  "use strict";
  // The id must differ from the slug of this page's own heading.
  var frame = document.getElementById("pytestmut-report-frame");
  // Grow the frame to the report's height so the page scrolls as one. Pages
  // opened from disk cannot see into the frame; they keep the fixed height.
  function fit() {
    try {
      var root = frame.contentDocument && frame.contentDocument.documentElement;
      if (root) { frame.style.height = root.scrollHeight + "px"; }
    } catch (error) {
      return;
    }
  }
  frame.addEventListener("load", function () {
    fit();
    try {
      if (window.ResizeObserver && frame.contentDocument) {
        new ResizeObserver(fit).observe(frame.contentDocument.body);
      }
    } catch (error) {
      return;
    }
  });
})();
</script>

[Open the report on its own page](../mutation-report/){ target="_blank" }
