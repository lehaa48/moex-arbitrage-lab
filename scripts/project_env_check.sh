# Fail-closed bootstrap checker (ADR-0002). Adapted from moex-event-alpha
# scripts/project_env_check.sh at commit 068bbd8d86a935686d741ea0ef1949617f9f0cab
# (migration row M-003). Do not source or run this file directly: scripts/project_env.sh
# starts it as a clean child process,
#     /usr/bin/env.exe -i PWD=... /usr/bin/bash.exe --noprofile --norc -- project_env_check.sh
# so the caller's shell functions (also exported ones), aliases, PATH, and startup files
# cannot reach it. It prints shell code for the sourced wrapper to evaluate:
# `\export NAME=value` lines on success, then `_mal_rc=0`; on refusal only `_mal_rc=1`
# (the reason goes to stderr). It exits 0 whenever it printed a result.
#
# Every check runs before the only write (trusted /usr/bin/mkdir.exe by absolute path).
# System32 is the OS-reported system directory (GetSystemDirectoryW via this Git Bash's
# own /usr/bin/cygpath.exe -S), never SYSTEMROOT, WINDIR, COMSPEC, or a PATH lookup.
# Each tool's stdout and stderr are captured together and its exit status checked.
# Missing tools, non-zero exits, and unexpected output all refuse. A local process that
# swaps a checked directory for a link after the checks (TOCTOU) is not excluded. Keep the
# emitted variables in sync with TOOL_LOCATION_ENV in
# src/moex_arbitrage_lab/settings/storage_paths.py (tests/unit/test_bootstrap_scripts.py
# compares them).

# Defense in depth, before this script defines anything: the process must be the clean
# child described above (no functions, no startup file variables).
if [[ -n "$(declare -F)" || -n "${BASH_ENV-}" || -n "${ENV-}" ]]; then
    echo "project_env refused (ADR-0002): checker is not running in a clean shell. Nothing was created or changed." >&2
    printf '%s\n' '_mal_rc=1'
    exit 0
fi

# One `\export NAME=value` line; %q quotes the value for the wrapper's eval.
_mal_emit() {
    printf '\\export %s=%q\n' "$1" "$2"
}

_mal_check_all() {
    local required_root='D:\project\moex-arbitrage-lab'  # ADR-0002 required root
    # Relative to the root. Existing components are checked; missing ones are created.
    local tool_dirs=(cache/uv cache/pre-commit cache/ruff cache/mypy cache/pytest
        cache/coverage cache/pycache tmp logs)
    # Checked but never created by this script.
    local checked_only=(.venv data artifacts reports)
    local script_dir root root_mixed root_win drive current part rest relative
    local pyproject name_found override
    local mkdir_tool=/usr/bin/mkdir.exe  # trusted mkdir

    _mal_refuse() {
        echo "project_env refused (ADR-0002): $1. Nothing was created or changed." >&2
    }
    # Succeeds if the path is missing or a plain directory; refuses links, junctions, files.
    _mal_plain_dir_or_missing() {
        if [ -L "$1" ]; then _mal_refuse "link, junction, or reparse point: $1"; return 1; fi
        if [ -e "$1" ] && [ ! -d "$1" ]; then _mal_refuse "not a directory: $1"; return 1; fi
        return 0
    }

    script_dir=${BASH_SOURCE[0]%/*}
    [ "$script_dir" = "${BASH_SOURCE[0]}" ] && script_dir=.
    # Logical path (builtins only): links in the path are not resolved, so they are
    # detected below. Only the Git Bash drive form /d/... is accepted.
    root=$(cd -L -- "$script_dir/.." 2>/dev/null && pwd -L) || {
        _mal_refuse "cannot enter the root"; return 1; }
    if [[ ! "$root" =~ ^/([A-Za-z])(/.+)$ ]]; then
        _mal_refuse "root is not a Git Bash drive path: $root"; return 1
    fi
    local drive_mount="/${BASH_REMATCH[1]}"
    root_mixed="${BASH_REMATCH[1]^^}:${BASH_REMATCH[2]}"
    root_win=${root_mixed//\//\\}
    case "$root_win" in
        [A-Za-z]:\\?*) ;;
        *) _mal_refuse "root is not an absolute drive path: $root_win"; return 1 ;;
    esac
    drive=${root_win:0:2}
    if [ "${drive,,}" = "c:" ]; then
        _mal_refuse "project storage on drive C: is refused: $root_win"; return 1
    fi
    if [ "${root_win,,}" != "${required_root,,}" ]; then
        _mal_refuse "root is $root_win; required $required_root"; return 1
    fi

    # Trust anchor for System32: the OS-reported system directory from `cygpath -S`, taken
    # by absolute path from this Git Bash installation's own /usr/bin.
    local discovery_tool=/usr/bin/cygpath.exe
    local system_dir system32 tool drive_type subst_list line
    local subst_line_re='^[A-Za-z]:\\: => .+$'
    if [ -L "$discovery_tool" ] || [ ! -f "$discovery_tool" ]; then
        _mal_refuse "system directory discovery tool missing or not a plain file"; return 1
    fi
    system_dir=$("$discovery_tool" -S -w 2>&1) || {
        _mal_refuse "cannot determine the Windows system directory"; return 1; }
    system_dir=${system_dir%$'\r'}
    if [[ "$system_dir" == *$'\n'* ]] || [[ ! "${system_dir,,}" =~ ^[a-z]:\\.+\\system32$ ]]; then
        _mal_refuse "cannot determine the Windows system directory"; return 1
    fi
    system32=${system_dir//\\//}
    if [ -L "$system32" ] || [ ! -d "$system32" ]; then
        _mal_refuse "Windows system directory missing or not a plain directory"; return 1
    fi
    for tool in fsutil.exe subst.exe; do
        if [ -L "$system32/$tool" ] || [ ! -f "$system32/$tool" ]; then
            _mal_refuse "system tool missing or not a plain file: $system32/$tool"; return 1
        fi
    done

    drive_type=$("$system32/fsutil.exe" fsinfo drivetype "$drive" 2>&1) || {
        _mal_refuse "cannot determine the type of drive $drive (fsutil.exe failed)"; return 1; }
    drive_type=${drive_type%$'\r'}
    if [ "${drive_type,,}" = "${drive,,} - fixed drive" ]; then
        :
    elif [[ "$drive_type" != *$'\n'* && "${drive_type,,}" == "${drive,,} - "* ]]; then
        _mal_refuse "drive $drive is not a fixed local drive"; return 1
    else
        _mal_refuse "unexpected fsutil.exe output"; return 1
    fi
    subst_list=$("$system32/subst.exe" 2>&1) || {
        _mal_refuse "cannot list subst drives (subst.exe failed)"; return 1; }
    while [ -n "$subst_list" ]; do
        line=${subst_list%%$'\n'*}
        if [ "$line" = "$subst_list" ]; then subst_list=""; else subst_list=${subst_list#*$'\n'}; fi
        line=${line%$'\r'}
        [ -z "${line//[[:space:]]/}" ] && continue
        if [[ ! "$line" =~ $subst_line_re ]]; then
            _mal_refuse "unexpected subst.exe output"; return 1
        fi
        if [[ "${line,,}" == "${drive,,}\\: =>"* ]]; then
            _mal_refuse "drive $drive is a subst alias"; return 1
        fi
    done

    # Every component from the drive down to the root must be a plain existing directory.
    current=$drive_mount
    rest=${root_win:3}
    while [ -n "$rest" ]; do
        part=${rest%%\\*}
        if [ "$part" = "$rest" ]; then rest=""; else rest=${rest#*\\}; fi
        current="${current%/}/$part"
        _mal_plain_dir_or_missing "$current" || return 1
        if [ ! -d "$current" ]; then _mal_refuse "missing: $current"; return 1; fi
    done

    if [ -L "$root/.git" ] || [ ! -d "$root/.git" ]; then
        _mal_refuse "not the main Git checkout (.git must be a directory; worktrees are refused)"
        return 1
    fi
    if [ -L "$root/pyproject.toml" ] || [ ! -f "$root/pyproject.toml" ] ||
        [ ! -r "$root/pyproject.toml" ]; then
        _mal_refuse "pyproject.toml missing or not a plain file"; return 1
    fi
    pyproject=$(< "$root/pyproject.toml") || {
        _mal_refuse "cannot read pyproject.toml"; return 1; }
    name_found=no
    while [ -n "$pyproject" ]; do
        line=${pyproject%%$'\n'*}
        if [ "$line" = "$pyproject" ]; then pyproject=""; else pyproject=${pyproject#*$'\n'}; fi
        line=${line%$'\r'}
        if [[ "$line" =~ ^[[:space:]]*name[[:space:]]*=[[:space:]]*\"moex-arbitrage-lab\"[[:space:]]*$ ]]; then
            name_found=yes
        fi
    done
    if [ "$name_found" != yes ]; then
        _mal_refuse "pyproject.toml does not name moex-arbitrage-lab"; return 1
    fi

    # An optional storage-root override may only repeat the required root.
    override=${MOEX_ARBITRAGE_STORAGE_ROOT-}
    override=${override%\\}
    if [ -n "$override" ] && [ "${override,,}" != "${required_root,,}" ]; then
        _mal_refuse "MOEX_ARBITRAGE_STORAGE_ROOT is set to a different location"; return 1
    fi

    for relative in "${tool_dirs[@]}" "${checked_only[@]}"; do
        current=$root
        rest=$relative
        while [ -n "$rest" ]; do
            part=${rest%%/*}
            if [ "$part" = "$rest" ]; then rest=""; else rest=${rest#*/}; fi
            current="$current/$part"
            _mal_plain_dir_or_missing "$current" || return 1
            [ -d "$current" ] || break
        done
    done

    # The only write: the trusted mkdir of this Git Bash installation, by absolute path.
    if [[ -L "$mkdir_tool" || ! -f "$mkdir_tool" ]]; then
        _mal_refuse "trusted mkdir missing or not a plain file: $mkdir_tool"; return 1
    fi
    for relative in "${tool_dirs[@]}"; do
        "$mkdir_tool" -p -- "$root/$relative" || {
            _mal_refuse "cannot create $relative (trusted mkdir failed)"; return 1; }
        _mal_plain_dir_or_missing "$root/$relative" || return 1
    done
    _mal_emit UV_CACHE_DIR "$root_mixed/cache/uv"
    _mal_emit PRE_COMMIT_HOME "$root_mixed/cache/pre-commit"
    _mal_emit RUFF_CACHE_DIR "$root_mixed/cache/ruff"
    _mal_emit MYPY_CACHE_DIR "$root_mixed/cache/mypy"
    _mal_emit COVERAGE_FILE "$root_mixed/cache/coverage/coverage-data"
    _mal_emit PYTHONPYCACHEPREFIX "$root_mixed/cache/pycache"
    _mal_emit PYTEST_ADDOPTS "-o cache_dir=$root_mixed/cache/pytest"
    _mal_emit TMP "$root_mixed/tmp"
    _mal_emit TEMP "$root_mixed/tmp"
    _mal_emit TMPDIR "$root_mixed/tmp"
    _mal_emit UV_PROJECT_ENVIRONMENT "$root_mixed/.venv"
    # Never download a Python interpreter implicitly: uv would install it outside the repository.
    _mal_emit UV_PYTHON_DOWNLOADS never
}

# Exports are emitted only if every check and every mkdir succeeded.
_mal_result=$(_mal_check_all)
_mal_status=$?
if [[ $_mal_status -eq 0 ]]; then
    printf '%s\n' "$_mal_result" '_mal_rc=0'
else
    printf '%s\n' '_mal_rc=1'
fi
exit 0
