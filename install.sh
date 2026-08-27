#!/usr/bin/env bash
# VektorCode installer.
#
#   curl -fsSL https://raw.githubusercontent.com/PAPA-CHUWE/vektorcode/main/install.sh | bash
#
# Installs via pipx so VektorCode's dependencies stay isolated from the
# projects it works on. Falls back to `pip install --user` if pipx cannot be
# installed. Set VEKTORCODE_SOURCE to install from somewhere other than PyPI.

set -euo pipefail

SOURCE="${VEKTORCODE_SOURCE:-vektorcode}"
MIN_MAJOR=3
MIN_MINOR=10

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  \033[34m●\033[0m %s\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }

bold "Installing VektorCode"

# ── Python ──────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import sys; sys.exit(0 if sys.version_info[:2] >= ($MIN_MAJOR, $MIN_MINOR) else 1)" 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done

[ -n "$PYTHON" ] || die "Python ${MIN_MAJOR}.${MIN_MINOR}+ is required but was not found. Install it and re-run."
ok "Found $("$PYTHON" --version 2>&1)"

# ── git (optional) ──────────────────────────────────────────────────
if command -v git >/dev/null 2>&1; then
    ok "Found $(git --version)"
else
    warn "git is not on PATH — the git tool will be unavailable; everything else works."
fi

# ── pipx ────────────────────────────────────────────────────────────
if command -v pipx >/dev/null 2>&1; then
    ok "Found pipx"
    INSTALLER="pipx"
else
    info "pipx not found — installing it"
    if "$PYTHON" -m pip install --user --quiet pipx >/dev/null 2>&1; then
        "$PYTHON" -m pipx ensurepath >/dev/null 2>&1 || true
        # ensurepath edits the shell profile but not this shell.
        export PATH="$HOME/.local/bin:$PATH"
        if command -v pipx >/dev/null 2>&1; then
            ok "Installed pipx"
            INSTALLER="pipx"
        else
            warn "pipx installed but not yet on PATH — falling back to pip"
            INSTALLER="pip"
        fi
    else
        warn "Could not install pipx — falling back to pip"
        INSTALLER="pip"
    fi
fi

# ── VektorCode ──────────────────────────────────────────────────────
info "Installing $SOURCE"
if [ "$INSTALLER" = "pipx" ]; then
    pipx install --force "$SOURCE" >/dev/null || die "pipx install failed. Try: pipx install $SOURCE"
else
    "$PYTHON" -m pip install --user --upgrade --quiet "$SOURCE" || die "pip install failed. Try: $PYTHON -m pip install --user $SOURCE"
fi

command -v vektorcode >/dev/null 2>&1 \
    && ok "Installed $(vektorcode --version 2>/dev/null || echo vektorcode)" \
    || warn "Installed, but 'vektorcode' is not on PATH yet. Open a new shell, or add ~/.local/bin to PATH."

# ── Configuration ───────────────────────────────────────────────────
CONFIG="$HOME/.config/vektorcode/.env"
if [ -f "$CONFIG" ]; then
    ok "Config found at $CONFIG"
else
    warn "No config yet. Create $CONFIG with:"
    printf '\n      LITELLM_BASE_URL=http://your-endpoint:30080\n'
    printf '      LITELLM_API_KEY=your-key\n'
    printf '      LITELLM_MODEL=your-model\n\n'
fi

bold "Done — run 'vektorcode' in any project to get started."
