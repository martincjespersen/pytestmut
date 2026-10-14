# pytestmut activation shim.
#
# pytestmut prepends this directory to PYTHONPATH for the test processes it
# starts, so every interpreter they launch imports this module at startup.
# That is how a mutant stays active in subprocesses and multiprocessing
# children. The rules for this file:
#   - it must parse on any Python that might inherit PYTHONPATH,
#   - it must never raise and never print,
#   - it does nothing unless PYTESTMUT_JOB is set,
#   - it hands over to whichever sitecustomize it is shadowing.
import os as _os
import sys as _sys


def _activate():
    if not _os.environ.get("PYTESTMUT_JOB"):
        return
    try:
        import _pytestmut_boot

        _pytestmut_boot.install()
    except Exception:
        pass


def _chain():
    try:
        import importlib.util
        from importlib.machinery import PathFinder

        here = _os.path.dirname(_os.path.abspath(__file__))
        others = [
            entry
            for entry in _sys.path
            if _os.path.abspath(entry or _os.getcwd()) != here
        ]
        spec = PathFinder.find_spec("sitecustomize", others)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        _sys.modules["sitecustomize"] = module
        spec.loader.exec_module(module)
    except Exception:
        pass


_activate()
_chain()
