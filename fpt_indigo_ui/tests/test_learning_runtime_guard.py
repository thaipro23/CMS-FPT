import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = REPO_ROOT / "scripts" / "fpt_learning_runtime_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("fpt_learning_runtime_guard", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lock(version):
    return {
        "packages": {
            "node_modules/@edx/frontend-component-header": {
                "version": version,
            }
        }
    }


def test_rejects_learning_runtime_without_header_actions_slot():
    guard = _load_guard()

    try:
        guard.validate_header_version(_lock("8.0.0"))
    except ValueError as exc:
        assert "8.2.1" in str(exc)
        assert "8.0.0" in str(exc)
    else:
        raise AssertionError("Learning header 8.0.0 must be rejected")


def test_accepts_runtime_clean_learning_header_version():
    guard = _load_guard()

    assert guard.validate_header_version(_lock("8.2.1")) == "8.2.1"
