#!/usr/bin/env bash
# Build `GammonView-Installer.exe`.
#
# Runs on macOS, Linux, or Windows under Git Bash -- anywhere the .NET SDK and
# curl are. The exe targets .NET Framework 4.8, which every Windows 10 and 11
# machine already has, and the Framework's reference assemblies come from NuGet,
# so nothing about the build needs Windows. CI builds the release on a Windows
# runner anyway, so the machine that compiles it is the kind it is for.
#
#   launcher/windows/build.sh                  # -> build/GammonView-Installer.exe
#   VERSION=1.0.0 launcher/windows/build.sh    # stamp a version
#
# One file comes out, with `uv` inside it as an embedded resource: a download
# that is a zip has to be extracted before it is run, and on Windows the
# failure when it is not is silent -- Explorer runs the exe from a temporary
# copy that disappears.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/build"
VERSION="${VERSION:-0.0.0}"
DOTNET="${DOTNET:-dotnet}"

# Pinned, not `latest`, and the same pin as the macOS build: a frozen installer
# built twice must be the same installer.
UV_VERSION="${UV_VERSION:-0.12.18}"
UV_ASSET="uv-x86_64-pc-windows-msvc.zip"
UV_BASE="https://github.com/astral-sh/uv/releases/download/$UV_VERSION"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

sha256() {
    if command -v sha256sum >/dev/null; then sha256sum "$1" | awk '{print $1}'
    else shasum -a 256 "$1" | awk '{print $1}'; fi
}

rm -rf "$BUILD"
mkdir -p "$BUILD"

# --- uv, x64 only -------------------------------------------------------------
#
# x64 only, and that is a decision about Python rather than about uv. bgsage
# publishes Windows wheels for AMD64 and nothing else, so on an ARM64 PC the
# helper has to run an x64 interpreter under emulation either way -- and an x64
# uv asks for an x64 Python by default, where a native ARM64 uv would fetch an
# ARM64 one that then cannot install the engine.
say "fetching $UV_ASSET ($UV_VERSION)"
curl -fsSL "$UV_BASE/$UV_ASSET" -o "$BUILD/uv.zip"
curl -fsSL "$UV_BASE/$UV_ASSET.sha256" -o "$BUILD/uv.zip.sha256"
# Verified rather than trusted: this binary is about to be copied onto
# somebody else's computer and run there.
want="$(awk '{print $1}' "$BUILD/uv.zip.sha256")"
got="$(sha256 "$BUILD/uv.zip")"
if [ "$want" != "$got" ]; then
    echo "error: $UV_ASSET checksum mismatch (want $want, got $got)" >&2
    exit 1
fi

# --- the installer ------------------------------------------------------------
say "compiling GammonView-Installer.exe ($VERSION)"
"$DOTNET" build "$HERE/GammonViewInstaller.csproj" -c Release --nologo \
    -p:Version="$VERSION" -p:UvZip="$BUILD/uv.zip" \
    -o "$BUILD/out"

cp "$BUILD/out/GammonView-Installer.exe" "$BUILD/GammonView-Installer.exe"
(cd "$BUILD" && sha256 GammonView-Installer.exe > GammonView-Installer.exe.sha256)

say "built $BUILD/GammonView-Installer.exe"
ls -l "$BUILD/GammonView-Installer.exe" | awk '{print "    " $5 " bytes"}'
