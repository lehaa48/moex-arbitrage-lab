# pre-commit entry point (ADR-0002): run the fail-closed bootstrap first, then the full
# offline P0 quality gate. Adapted from moex-event-alpha scripts/guarded_quality_gate.ps1 at
# commit 068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-005).
# .pre-commit-config.yaml invokes it with a process-scoped execution policy:
#     powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File scripts/guarded_quality_gate.ps1
#
# The bootstrap script itself locates uv by absolute path (.NET, not command lookup), starts
# it with CreateProcess in the repository root, and passes the exit code on.

$ErrorActionPreference = 'Stop'
& ([System.IO.Path]::Combine($PSScriptRoot, 'project_env.ps1')) -Task quality-gate
exit $LASTEXITCODE
