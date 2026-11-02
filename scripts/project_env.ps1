# Fail-closed bootstrap: verify the project root, then redirect tool caches, temp and the
# virtualenv into the repository (ADR-0002, docs/STORAGE_AND_SECRETS_POLICY.md).
# Adapted from moex-event-alpha scripts/project_env.ps1 at commit
# 068bbd8d86a935686d741ea0ef1949617f9f0cab (migration row M-002).
#
# Interactive use (current PowerShell process only; no CurrentUser/LocalMachine change):
#
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force
#     . .\scripts\project_env.ps1
#
# Task use (VS Code tasks, run with -File in a child powershell.exe):
#
#     -Bootstrap                 uv sync --locked, then the storage report
#     -Task verify-storage       storage/path report (offline)
#     -Task tests                relevant P0 tests (offline)
#     -Task quality-gate         full offline P0 quality gate
#     -Task stage -Stage P1 -Entry "<module> <args>"
#                                future stage entrypoint; fails clearly while it is absent
#
# Every check below runs before anything is created or any variable is set. On refusal the
# script throws, creates nothing, and leaves the environment unchanged. It changes only the
# current process environment, never global Windows variables. A local process that swaps a
# checked directory for a link after the checks (TOCTOU) is not excluded.
#
# The calling session cannot intercept this script: it calls no cmdlet, function, or alias
# by name. Only language keywords, operators, direct .NET calls and scriptblock variables are
# used. Keep the exported variables in sync with TOOL_LOCATION_ENV in
# src/moex_arbitrage_lab/settings/storage_paths.py (tests/unit/test_bootstrap_scripts.py
# compares them).

param(
    [switch]$Bootstrap,
    [ValidateSet('', 'bootstrap', 'verify-storage', 'tests', 'quality-gate', 'stage')]
    [string]$Task = '',
    [string]$Stage = '',
    [string]$Entry = ''
)

& {
    $ErrorActionPreference = 'Stop'
    $RequiredRoot = 'D:\project\moex-arbitrage-lab'  # ADR-0002 required root
    # Relative to the root. Existing components are checked; missing ones are created.
    $ToolDirs = @(
        'cache\uv', 'cache\pre-commit', 'cache\ruff', 'cache\mypy', 'cache\pytest',
        'cache\coverage', 'cache\pycache', 'tmp', 'logs'
    )
    # Checked but never created by this script.
    $CheckedOnly = @('.venv', 'data', 'artifacts', 'reports')
    $ReparsePoint = [System.IO.FileAttributes]::ReparsePoint
    $DirectoryAttribute = [System.IO.FileAttributes]::Directory

    $Stop = {
        param([string]$Reason)
        throw "project_env refused (ADR-0002): $Reason. Nothing was created or changed."
    }
    # Attributes of $Path without following links, or $null if it does not exist.
    $GetAttributes = {
        param([string]$Path)
        try {
            return [System.IO.File]::GetAttributes($Path)
        } catch [System.IO.FileNotFoundException], [System.IO.DirectoryNotFoundException] {
            return $null
        } catch {
            & $Stop "cannot inspect $Path"
        }
    }
    # $true for a plain directory, $false if missing; refuses links, junctions, and files.
    $IsPlainDirectory = {
        param([string]$Path)
        $attributes = & $GetAttributes $Path
        if ($null -eq $attributes) { return $false }
        if ($attributes -band $ReparsePoint) { & $Stop "link, junction, or reparse point: $Path" }
        if (-not ($attributes -band $DirectoryAttribute)) { & $Stop "not a directory: $Path" }
        return $true
    }
    # $true only for an existing plain file (not a directory, link, or reparse point).
    $IsPlainFile = {
        param([string]$Path)
        $attributes = & $GetAttributes $Path
        return ($null -ne $attributes -and -not ($attributes -band $DirectoryAttribute) -and
            -not ($attributes -band $ReparsePoint))
    }

    if ([string]::IsNullOrEmpty($PSScriptRoot)) { & $Stop 'script location unknown' }
    # Lexical parent of scripts\: links in the path are not followed, so they are detected.
    $root = [System.IO.Path]::GetDirectoryName($PSScriptRoot)
    if ($root -notmatch '^[A-Za-z]:\\[^\\]') {
        & $Stop "root is not an absolute drive path: $root"
    }
    $drive = $root.Substring(0, 2)
    if ([string]::Equals($drive, 'C:', [System.StringComparison]::OrdinalIgnoreCase)) {
        & $Stop "project storage on drive C: is refused: $root"
    }
    if (-not [string]::Equals($root, $RequiredRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        & $Stop "root is $root; required $RequiredRoot"
    }

    # Drive type from the OS (GetDriveType through .NET), not from a tool or variable.
    if ([System.IO.DriveInfo]::new($drive).DriveType -ne [System.IO.DriveType]::Fixed) {
        & $Stop "drive $drive is not a fixed local drive"
    }

    # Trust anchor for System32: the OS-reported system directory (GetSystemDirectoryW
    # through .NET). SystemRoot, windir, ComSpec, and PATH are never consulted.
    $SystemDirectory = [Environment]::SystemDirectory
    if ([string]::IsNullOrEmpty($SystemDirectory) -or
        $SystemDirectory -notmatch '^[A-Za-z]:\\[^\r\n]+\\system32$') {
        & $Stop 'cannot determine the Windows system directory'
    }
    $substExe = [System.IO.Path]::Combine($SystemDirectory, 'subst.exe')
    if (-not (& $IsPlainFile $substExe)) {
        & $Stop "system tool missing or not a plain file: $substExe"
    }
    # Started with CreateProcess directly (UseShellExecute = false). A start failure, any
    # stderr output, a non-zero exit, or any line that is not a subst mapping refuses.
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new($substExe)
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    try {
        $process = [System.Diagnostics.Process]::Start($startInfo)
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        $exitCode = $process.ExitCode
    } catch {
        & $Stop 'cannot list subst drives (subst.exe failed to run)'
    }
    if ($exitCode -ne 0 -or $stderr.Trim().Length -gt 0) {
        & $Stop 'cannot list subst drives (subst.exe failed)'
    }
    foreach ($line in $stdout -split "`r?`n") {
        if ($line.Trim().Length -eq 0) { continue }
        if ($line -notmatch '^[A-Za-z]:\\: => .+$') {
            & $Stop 'unexpected subst.exe output'
        }
        if ($line.StartsWith("$drive\: =>", [System.StringComparison]::OrdinalIgnoreCase)) {
            & $Stop "drive $drive is a subst alias"
        }
    }

    # Every component from the drive down to the root must be a plain existing directory.
    $current = "$drive\"
    foreach ($part in $root.Substring(3).Split('\')) {
        $current = [System.IO.Path]::Combine($current, $part)
        if (-not (& $IsPlainDirectory $current)) { & $Stop "missing: $current" }
    }

    $gitAttributes = & $GetAttributes ([System.IO.Path]::Combine($root, '.git'))
    if ($null -eq $gitAttributes -or -not ($gitAttributes -band $DirectoryAttribute) -or
        ($gitAttributes -band $ReparsePoint)) {
        & $Stop 'not the main Git checkout (.git must be a directory; worktrees are refused)'
    }
    $pyproject = [System.IO.Path]::Combine($root, 'pyproject.toml')
    if (-not (& $IsPlainFile $pyproject)) {
        & $Stop 'pyproject.toml missing or not a plain file'
    }
    $text = [System.IO.File]::ReadAllText($pyproject)
    if ($text -notmatch '(?m)^\s*name\s*=\s*"moex-arbitrage-lab"\s*$') {
        & $Stop 'pyproject.toml does not name moex-arbitrage-lab'
    }

    # An optional storage-root override may only repeat the required root.
    $override = [System.Environment]::GetEnvironmentVariable('MOEX_ARBITRAGE_STORAGE_ROOT')
    if (-not [string]::IsNullOrEmpty($override) -and
        -not [string]::Equals($override.TrimEnd('\'), $RequiredRoot,
            [System.StringComparison]::OrdinalIgnoreCase)) {
        & $Stop 'MOEX_ARBITRAGE_STORAGE_ROOT is set to a different location'
    }

    foreach ($relative in $ToolDirs + $CheckedOnly) {
        $current = $root
        foreach ($part in $relative.Split('\')) {
            $current = [System.IO.Path]::Combine($current, $part)
            if (-not (& $IsPlainDirectory $current)) { break }
        }
    }

    # All checks passed: create the tool directories, re-check them, then export.
    foreach ($relative in $ToolDirs) {
        $path = [System.IO.Path]::Combine($root, $relative)
        [void][System.IO.Directory]::CreateDirectory($path)
        if (-not (& $IsPlainDirectory $path)) { & $Stop "cannot create $path" }
    }
    $cache = [System.IO.Path]::Combine($root, 'cache')
    $temp = [System.IO.Path]::Combine($root, 'tmp')
    $exports = [ordered]@{
        UV_CACHE_DIR = [System.IO.Path]::Combine($cache, 'uv')
        PRE_COMMIT_HOME = [System.IO.Path]::Combine($cache, 'pre-commit')
        RUFF_CACHE_DIR = [System.IO.Path]::Combine($cache, 'ruff')
        MYPY_CACHE_DIR = [System.IO.Path]::Combine($cache, 'mypy')
        COVERAGE_FILE = [System.IO.Path]::Combine($cache, 'coverage\coverage-data')
        PYTHONPYCACHEPREFIX = [System.IO.Path]::Combine($cache, 'pycache')
        # pytest splits PYTEST_ADDOPTS like a POSIX shell, so the path uses forward slashes.
        PYTEST_ADDOPTS = '-o cache_dir=' + [System.IO.Path]::Combine($cache, 'pytest').Replace('\', '/')
        TMP = $temp
        TEMP = $temp
        TMPDIR = $temp
        UV_PROJECT_ENVIRONMENT = [System.IO.Path]::Combine($root, '.venv')
        # Never download a Python interpreter implicitly: uv would install it outside the
        # repository (and contact a host other than the Python package index).
        UV_PYTHON_DOWNLOADS = 'never'
    }
    # Message first, exports last: a failure here still leaves nothing exported.
    $Host.UI.WriteLine("Project caches, temp and virtualenv redirected under $root")
    foreach ($name in $exports.Keys) {
        [System.Environment]::SetEnvironmentVariable(
            $name, $exports[$name], [System.EnvironmentVariableTarget]::Process)
    }
}

$SelectedTask = if ($Bootstrap) { 'bootstrap' } else { $Task }
if ($SelectedTask -eq '') { return }

# Task mode (-File only): run uv by absolute path with CreateProcess, inherit the guarded
# environment and pass the exit code on. Any error here ends the task with exit 1.
& {
    $ErrorActionPreference = 'Stop'
    trap {
        $Host.UI.WriteErrorLine("project_env task failed: $_")
        exit 1
    }
    $root = [System.IO.Path]::GetDirectoryName($PSScriptRoot)
    $uv = $null
    foreach ($directory in [System.Environment]::GetEnvironmentVariable('PATH').Split(';')) {
        if ($directory.Length -eq 0) { continue }
        $candidate = [System.IO.Path]::Combine($directory.Trim('"'), 'uv.exe')
        if ([System.IO.File]::Exists($candidate)) { $uv = $candidate; break }
    }
    if ($null -eq $uv) {
        $Host.UI.WriteErrorLine('project_env: uv.exe not found on PATH')
        exit 1
    }
    $offline = 'run --locked --offline python'
    $commands = switch ($SelectedTask) {
        'bootstrap' { @('sync --locked', "$offline scripts/check_storage_paths.py") }
        'verify-storage' { @("$offline scripts/check_storage_paths.py") }
        'tests' { @("$offline scripts/quality_gate.py --scope changed") }
        'quality-gate' { @("$offline scripts/quality_gate.py --scope full") }
        'stage' {
            # Only plain tokens: no quotes, no shell metacharacters, nothing to expand.
            if ($Stage -notmatch '^P[1-4]$' -or $Entry -notmatch '^[A-Za-z0-9_./ -]+$') {
                $Host.UI.WriteErrorLine('project_env: -Stage P1..P4 and a plain -Entry are required')
                exit 2
            }
            @("$offline scripts/stage_entrypoint.py $Stage $Entry")
        }
    }
    foreach ($arguments in $commands) {
        $Host.UI.WriteLine("==> uv $arguments")
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new($uv)
        $startInfo.Arguments = $arguments
        $startInfo.WorkingDirectory = $root
        $startInfo.UseShellExecute = $false
        $process = [System.Diagnostics.Process]::Start($startInfo)
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            $Host.UI.WriteErrorLine("project_env: 'uv $arguments' failed (exit $($process.ExitCode))")
            exit $process.ExitCode
        }
    }
    exit 0
}
