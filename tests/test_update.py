# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""Whether the menu offers an update, and what installing it runs."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import respx

from gvhelper import update


@respx.mock
def test_the_release_is_whatever_the_site_pins(cfg):
    respx.get("https://example.test/helper-release.json").mock(
        return_value=httpx.Response(200, json={"version": "0.5.1", "python": "3.13"})
    )
    assert update.latest(cfg)["version"] == "0.5.1"


@respx.mock
def test_no_answer_is_no_update(cfg):
    respx.get("https://example.test/helper-release.json").mock(
        side_effect=httpx.ConnectError("offline")
    )
    assert update.latest(cfg) is None
    respx.get("https://example.test/helper-release.json").mock(
        return_value=httpx.Response(200, text="<html>")
    )
    assert update.latest(cfg) is None


def test_only_a_later_release_is_offered():
    assert update.newer("0.5.0", "0.4.0")
    assert update.newer("0.10.0", "0.9.3"), "compared as numbers, not text"
    assert not update.newer("0.4.0", "0.4.0")
    assert not update.newer("0.4.0", "0.5.0")
    assert not update.newer("0.5.0", "0.4.1.dev3+gabc"), "a dev build chose itself"
    assert not update.newer("0.5.0", "unknown")


def installer_layout(tmp_path):
    root = tmp_path / "GammonView"
    (root / "bin").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "bin" / "uv").write_text("")
    return root


def test_the_installers_install_is_recognised_by_its_uv(tmp_path):
    root = installer_layout(tmp_path)
    assert update.installer_root(str(root / "bin" / "gammonview-helper")) == root
    assert update.installer_root(str(tmp_path / ".local" / "bin" / "gammonview-helper")) is None


def test_the_windows_installers_install_is_recognised_by_its_uv_exe(tmp_path):
    root = installer_layout(tmp_path)
    (root / "bin" / "uv").unlink()
    (root / "bin" / "uv.exe").write_text("")
    assert update.installer_root(str(root / "bin" / "gammonview-helper.exe")) == root


def test_installing_runs_what_the_installer_runs(tmp_path):
    root = installer_layout(tmp_path)
    seen = {}

    def run(argv, **kw):
        seen["argv"], seen["env"] = argv, kw["env"]
        return subprocess.CompletedProcess(argv, 0, "", "")

    where = update.target(str(root / "bin" / "gammonview-helper"))
    assert update.install(where, {"version": "0.5.0", "python": "3.13"}, run) is None
    assert seen["argv"] == [
        str(root / "bin" / "uv"), "tool", "install", "--force",
        "--python", "3.13", "gammonview-helper==0.5.0",
    ]
    assert seen["env"]["UV_TOOL_DIR"] == str(root / "tools")
    assert seen["env"]["UV_PYTHON_PREFERENCE"] == "only-managed"


def test_a_failed_install_says_uvs_last_word(tmp_path):
    root = installer_layout(tmp_path)

    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", "Resolving...\nerror: no network\n")

    where = update.target(str(root / "bin" / "gammonview-helper"))
    assert update.install(where, {"version": "0.5.0"}, run) == "error: no network"


def uv_tool_layout(tmp_path):
    """What `uv tool install` leaves: a venv with a receipt, and an entry point."""
    tool = tmp_path / "share" / "uv" / "tools" / "gammonview-helper"
    tool.mkdir(parents=True)
    (tool / "uv-receipt.toml").write_text("[tool]\n")
    helper = tmp_path / "bin" / "gammonview-helper"
    helper.parent.mkdir()
    return tool, helper


def test_a_uv_tool_install_is_updated_with_the_persons_own_uv(tmp_path):
    tool, helper = uv_tool_layout(tmp_path)
    seen = {}

    def which(name, path):
        seen["path"] = path
        return "/somewhere/uv"

    where = update.target(str(helper), prefix=str(tool), environ={"PATH": "/usr/bin"}, which=which)
    assert where.uv == "/somewhere/uv"
    assert where.env["UV_TOOL_DIR"] == str(tool.parent), "the copy that is running"
    assert where.env["UV_TOOL_BIN_DIR"] == str(helper.parent)
    assert seen["path"].startswith("/usr/bin"), "PATH first"

    def run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "", "")

    update.install(where, {"version": "0.8.0", "python": "3.13"}, run)
    minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert seen["argv"] == [
        "/somewhere/uv", "tool", "install", "--force",
        "--python", minor, "gammonview-helper==0.8.0",
    ], "keeps its own Python, and installs exactly the pinned version"


def test_uv_is_looked_for_where_a_login_item_cannot_see_it(tmp_path):
    tool, helper = uv_tool_layout(tmp_path)
    seen = {}

    def which(name, path):
        seen["path"] = path
        return None

    assert update.target(str(helper), prefix=str(tool), environ={"PATH": "/usr/bin"}, which=which) is None
    searched = [Path(p) for p in seen["path"].split(os.pathsep)]
    assert Path.home() / ".local" / "bin" in searched


def test_anything_else_is_not_ours_to_update(tmp_path):
    venv = tmp_path / "pipx" / "venvs" / "gammonview-helper"
    venv.mkdir(parents=True)
    assert update.target(str(tmp_path / "bin" / "gammonview-helper"), prefix=str(venv),
                         which=lambda *a, **k: "/uv") is None


def test_dev_builds_are_not_releases():
    assert update.is_release("0.8.0")
    assert not update.is_release("0.7.1.dev0+g6d4b542.d20261007")


@pytest.mark.skipif(sys.platform == "darwin", reason="only off macOS")
def test_no_login_item_off_macos():
    assert update.restart_login_item(run=lambda *a, **k: pytest.fail("ran launchctl")) is False


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_the_login_item_is_restarted_onto_the_new_code(tmp_path, monkeypatch):
    monkeypatch.setattr(update.Path, "home", lambda: tmp_path)
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert update.restart_login_item(run) is False, "no login item, nothing to restart"
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.gammonview.helper.plist").write_text("")
    assert update.restart_login_item(run) is True
    assert seen[-1][:3] == ["launchctl", "kickstart", "-k"]
