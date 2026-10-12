#!/usr/bin/env bash
# Run the Windows launcher's checks (launcher/windows/Checks/Program.cs).
#
# Needs only the .NET SDK, on any OS -- the checks cover decisions, not the
# machine, so they run on macOS and Linux as happily as on Windows. Cheap
# enough for every push.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOTNET="${DOTNET:-dotnet}"
"$DOTNET" run --project "$HERE/Checks/Checks.csproj" -c Release --nologo
