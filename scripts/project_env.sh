# Fail-closed bootstrap: verify the project root, then redirect tool caches, temp and the
# virtualenv into the repository (ADR-0002). Adapted from moex-event-alpha
# scripts/project_env.sh at commit 068bbd8d86a935686d741ea0ef1949617f9f0cab (migration
# row M-003). Source it in Git Bash before any uv/python command; Claude Code's shell does
# not keep state between calls, so prefix each command:
#     . scripts/project_env.sh && uv run --locked --offline ...
#
# All checks and the only writes run in scripts/project_env_check.sh, started as a clean
# child: /usr/bin/env.exe -i (no inherited variables, so no exported functions) and
# /usr/bin/bash.exe --noprofile --norc (no startup files, no aliases), both by absolute path
# from this Git Bash installation. The child prints `\export` lines on success, then a
# result code; this wrapper evaluates that output. On refusal nothing is created, nothing
# is exported, and the caller's shell options are restored.
#
# This wrapper cannot be intercepted by the caller's shell:
# - It has no keywords and no unquoted command words. In a sourced file, aliases are
#   expanded even for keywords such as `if`.
# - Until POSIX mode is on, it runs only assignments. `POSIXLY_CORRECT=1` is a plain
#   assignment, and in POSIX mode the special builtins eval, export, and unset are found
#   before any shell function.
# - External programs are called only by quoted absolute path.
# - The result code is checked with `(( ))`, after POSIX mode is restored.
# - A caller running with `set -e` is safe: the launcher failure is caught by `||`, so
#   POSIX state is always restored before the non-zero result.
#
# The storage-root override MOEX_ARBITRAGE_STORAGE_ROOT is passed to the clean child
# explicitly; nothing else is inherited. Keep the emitted variables in sync with
# TOOL_LOCATION_ENV in src/moex_arbitrage_lab/settings/storage_paths.py.

# Command that restores the caller's POSIX state at the end: the caller's own value if
# POSIXLY_CORRECT was set, otherwise unset it (which also turns POSIX mode off again).
_mal_restore=${POSIXLY_CORRECT+POSIXLY_CORRECT=${POSIXLY_CORRECT@Q}}
_mal_restore=${_mal_restore:-'\unset POSIXLY_CORRECT'}
POSIXLY_CORRECT=1
# The checker sits next to this file. If the name does not end in project_env.sh, the
# path becomes empty and the child fails, so the bootstrap refuses.
_mal_checker=${BASH_SOURCE[0]/%project_env.sh/project_env_check.sh}
_mal_checker=${_mal_checker/#"${BASH_SOURCE[0]}"/}
# `|| _mal_code=''`: a failing launcher must not end a `set -e` caller here, before
# POSIX state is restored. The failure is kept as an empty result (refusal) and reported
# by the final non-zero `(( ))`, which may then end such a caller after the restore.
_mal_code=$('/usr/bin/env.exe' -i "PWD=$PWD" "MOEX_ARBITRAGE_STORAGE_ROOT=${MOEX_ARBITRAGE_STORAGE_ROOT-}" '/usr/bin/bash.exe' --noprofile --norc -- "$_mal_checker") || _mal_code=''
\eval "${_mal_code:-_mal_rc=1}"
\eval "\\unset _mal_code _mal_checker _mal_restore _mal_rc; ${_mal_restore}; (( ${_mal_rc:-1} == 0 ))"
