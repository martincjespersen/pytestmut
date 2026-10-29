"""Names and version numbers shared across the package.

The values that cross the process boundary (environment variable names, the
wire version) are duplicated in ``_shim/_pytestmut_boot.py``, which cannot
import this package. ``tests/test_wire_contract.py`` pins the two together.
"""

__version__ = "0.1.0"

TOOL_NAME = "pytestmut"
CONFIG_TABLE = "pytestmut"
STANDALONE_CONFIG = "pytestmut.toml"
STATE_DIR_NAME = ".pytestmut"
SENTINEL_NAME = ".pytestmut-state"

# Bumped whenever job.json or the record lines change shape.
WIRE_VERSION = 1
# Bumped whenever results.json changes shape.
RESULT_SCHEMA_VERSION = 2
# Bumped whenever the meaning of a cached verdict changes.
VERDICT_SEMANTICS_VERSION = 1

ENV_ACTIVE = "PYTESTMUT_ACTIVE"
ENV_JOB = "PYTESTMUT_JOB"
ENV_WORKER = "PYTESTMUT_WORKER"
ENV_RUN = "PYTESTMUT_RUN"
ENV_PROBE_OUT = "PYTESTMUT_PROBE_OUT"

RECORDER_PLUGIN = "_pytestmut_recorder"
PROBE_PLUGIN = "_pytestmut_probe"
