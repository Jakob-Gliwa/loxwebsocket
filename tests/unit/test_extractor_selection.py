"""Extractor selection logic (regression for commit a268b15, Alpine/musl).

The import-time selection in lox_ws_api picks the compatible extractor on
ARM/aarch64, the optimized one only when both AVX and AVX2 are present, and
must fall back to compatible if CPU-feature detection raises (which is what
broke on Alpine when it shelled out instead of using cpuinfo).

Each test reloads the module with a mocked CPU, asserts the choice, then
restores a clean module so it can't leak a fake ``parse_message`` into the
module globals shared by the other tests.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

import loxwebsocket.lox_ws_api as mod


def _install_fake_optimized(monkeypatch):
    fake = types.ModuleType("loxwebsocket.cython_modules.extractor_optimized")
    fake.parse_message = lambda message: {"impl": "optimized"}
    fake.parse_type_3_message = lambda message: {"impl": "optimized"}
    fake.convert_bytes_to_uuid = lambda raw: b"fake"
    monkeypatch.setitem(
        sys.modules, "loxwebsocket.cython_modules.extractor_optimized", fake
    )


def _reload_clean():
    """Undo everything by reloading with the real (unmocked) environment."""
    importlib.reload(mod)


@pytest.mark.parametrize("machine", ["aarch64", "arm64", "ARMv7"])
def test_arm_uses_compatible(monkeypatch, machine):
    monkeypatch.setattr("platform.machine", lambda: machine)
    try:
        reloaded = importlib.reload(mod)
        assert reloaded._EXTRACTOR_IMPL.startswith("compatible (arch=")
    finally:
        monkeypatch.undo()
        _reload_clean()


def test_x86_with_avx_and_avx2_uses_optimized(monkeypatch):
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setattr(
        "cpuinfo.get_cpu_info", lambda: {"flags": ["sse", "avx", "avx2"]}
    )
    _install_fake_optimized(monkeypatch)
    try:
        reloaded = importlib.reload(mod)
        assert reloaded._EXTRACTOR_IMPL == "optimized (avx+avx2)"
        assert reloaded.parse_message(b"") == {"impl": "optimized"}
    finally:
        monkeypatch.undo()
        _reload_clean()


def test_x86_missing_flags_uses_compatible(monkeypatch):
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setattr("cpuinfo.get_cpu_info", lambda: {"flags": ["sse", "avx"]})
    try:
        reloaded = importlib.reload(mod)
        assert reloaded._EXTRACTOR_IMPL == "compatible (missing avx/avx2)"
    finally:
        monkeypatch.undo()
        _reload_clean()


def test_detection_failure_falls_back_to_compatible(monkeypatch):
    monkeypatch.setattr("platform.machine", lambda: "x86_64")

    def boom():
        raise RuntimeError("cpuinfo unavailable (Alpine/musl)")

    monkeypatch.setattr("cpuinfo.get_cpu_info", boom)
    try:
        reloaded = importlib.reload(mod)
        assert reloaded._EXTRACTOR_IMPL == "compatible (detection failed)"
    finally:
        monkeypatch.undo()
        _reload_clean()


def test_module_restored_after_reload():
    # Sanity: after the clean reloads above, the module still exposes a working
    # real extractor (not a leaked fake).
    from support.loxone_builders import build_value_states, expected_value_dict

    items = [(bytes(range(16)), 5.0)]
    assert mod.parse_message(build_value_states(items)) == expected_value_dict(items)
