"""The C++ runtime loaded ahead of bgsage on Windows (`_gvhelper_msvc.py`)."""

import ctypes
import sys

import pytest

import _gvhelper_msvc as msvc


def test_windows_loads_the_bundled_cpp_runtime(monkeypatch, tmp_path):
    """bgsage crashes against an old system `msvcp140.dll`; ours must win."""
    loaded = []
    monkeypatch.setattr(msvc.os, "name", "nt")
    monkeypatch.setattr(msvc.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(ctypes, "WinDLL", loaded.append, raising=False)
    msvc.load()
    assert loaded == [str(tmp_path / "msvcp140.dll")]


def test_a_missing_bundled_runtime_is_not_fatal(monkeypatch):
    def missing(path):
        raise OSError(126, "not found")

    monkeypatch.setattr(msvc.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", missing, raising=False)
    msvc.load()


def test_off_windows_no_runtime_is_loaded(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")  # what other tests pretend
    monkeypatch.setattr(ctypes, "WinDLL", lambda path: pytest.fail(path), raising=False)
    msvc.load()
