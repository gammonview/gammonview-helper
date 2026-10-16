"""What every interpreter in the helper's environment does first (`_gvhelper_startup.py`)."""

import ctypes
import sys

import pytest

import _gvhelper_startup as startup


def test_windows_loads_the_bundled_cpp_runtime(monkeypatch, tmp_path):
    """bgsage crashes against an old system `msvcp140.dll`; ours must win."""
    loaded = []
    monkeypatch.setattr(startup.os, "name", "nt")
    monkeypatch.setattr(startup.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(ctypes, "WinDLL", loaded.append, raising=False)
    startup.load_msvc_runtime()
    assert loaded == [str(tmp_path / "msvcp140.dll")]


def test_a_missing_bundled_runtime_is_not_fatal(monkeypatch):
    def missing(path):
        raise OSError(126, "not found")

    monkeypatch.setattr(startup.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", missing, raising=False)
    startup.load_msvc_runtime()


def test_off_windows_no_runtime_is_loaded(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")  # what other tests pretend
    monkeypatch.setattr(ctypes, "WinDLL", lambda path: pytest.fail(path), raising=False)
    startup.load_msvc_runtime()


def test_a_windowless_worker_gets_the_log(monkeypatch, tmp_path):
    """`gvanalysis` enables faulthandler at import, which needs a stderr."""
    monkeypatch.setenv("GAMMONVIEW_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    startup.give_stderr()
    try:
        import faulthandler

        faulthandler.enable()
        faulthandler.disable()
        print("worker", file=sys.stderr)
    finally:
        sys.stderr.close()
    assert (tmp_path / "logs" / "helper.log").read_text(encoding="utf-8") == "worker\n"


def test_a_log_that_cannot_be_set_aside_is_still_written(monkeypatch, tmp_path):
    """A worker crossing the limit while the helper holds the file open."""
    from gvhelper import winlog

    log = tmp_path / "logs" / "helper.log"
    log.parent.mkdir(parents=True)
    log.write_text("x" * 20)
    monkeypatch.setenv("GAMMONVIEW_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(winlog, "LOG_LIMIT", 10)

    def in_use(self, target):
        raise PermissionError(32, "in use")

    monkeypatch.setattr(type(log), "replace", in_use)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    winlog.log_when_windowless()
    print("still here")
    sys.stdout.close()
    assert log.read_text() == "x" * 20 + "still here\n"
