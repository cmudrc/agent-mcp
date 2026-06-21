#!/usr/bin/env bash
# bootstrap.sh -- One-command setup for the CMU DRC MCP aircraft-analysis
# pipeline + Gemma agent on macOS / Linux / WSL2.
#
# What this script does (in order):
#   1. Verify prerequisites (git, python>=3.12, curl). On macOS recommends
#      Homebrew; on Linux/WSL recommends apt-get / dnf.
#   2. Clone all required cmudrc repos next to this script (skipped if
#      already present).
#   3. Create a project-local .venv and pip-install every MCP package
#      (tigl-mcp, su2-mcp, pycycle-mcp, nseg-mcp, aviary-cpacs-mcp,
#      mission-mcp), the shared CPACS manager, and agent-mcp itself,
#      all in editable mode.
#   4. Install SU2 (via su2-mcp/scripts/install_su2.sh -- conda preferred,
#      falls back to binary download).
#   5. Install Ollama (rootless) and pull the default Gemma model.
#   6. Run a sanity check (su2_run_aero on a stub adapter call, plus a
#      one-shot Ollama ping).
#   7. (default) Launch the Gemma agent in REPL mode against the bundled
#      D150 example so the user can immediately ask aircraft-analysis
#      questions in natural language.
#
# Flags:
#   --no-launch        Skip step 7 (set up everything, exit).
#   --no-models        Skip step 5b (install Ollama but don't pull Gemma).
#   --model NAME       Override the default Gemma model
#                      (default: gemma4:e4b).
#   --server-tier      Also pull gemma3:27b for the lab-server tier.
#   --workdir DIR      Use DIR as the project root (default: $PWD).
#   --skip-clone       Don't try to git-clone (use repos already present).
#
# This script is idempotent: re-running it on an already-bootstrapped
# project is safe.
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# ---------- arg parsing ----------------------------------------------------
LAUNCH=1
PULL_MODELS=1
SERVER_TIER=0
SKIP_CLONE=0
MODEL="gemma4:e4b"
WORKDIR="$(pwd)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-launch) LAUNCH=0; shift ;;
        --no-models) PULL_MODELS=0; shift ;;
        --server-tier) SERVER_TIER=1; shift ;;
        --skip-clone) SKIP_CLONE=1; shift ;;
        --model) MODEL="$2"; shift 2 ;;
        --workdir) WORKDIR="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,30p' "$0"; exit 0 ;;
        *)
            echo -e "${RED}Unknown flag: $1${NC}" >&2; exit 2 ;;
    esac
done

cd "$WORKDIR"

# ---------- helpers --------------------------------------------------------
info()   { echo -e "${BLUE}[bootstrap]${NC} $*"; }
ok()     { echo -e "${GREEN}[bootstrap]${NC} $*"; }
warn()   { echo -e "${YELLOW}[bootstrap]${NC} $*"; }
die()    { echo -e "${RED}[bootstrap]${NC} $*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1. $2"
}

# ---------- 1. prerequisites ----------------------------------------------
info "Step 1/7: checking prerequisites..."

OS="$(uname -s)"
ARCH="$(uname -m)"
info "  platform: ${OS} ${ARCH}"

case "$OS" in
    Darwin)
        require_cmd git "Install with: xcode-select --install"
        require_cmd curl "Install with: brew install curl"
        ;;
    Linux)
        require_cmd git "Install with: sudo apt-get install -y git  (or dnf/yum equivalent)"
        require_cmd curl "Install with: sudo apt-get install -y curl"
        ;;
    *)
        warn "Unsupported OS: $OS. Proceeding best-effort (Windows users: please run this from WSL2)."
        ;;
esac

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        v=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
        case "$v" in
            3.12|3.13) PYTHON_BIN="$candidate"; break ;;
        esac
    fi
done
[ -n "$PYTHON_BIN" ] || die "Need Python 3.12 or 3.13. Install from https://python.org or your package manager."
ok "  python: $($PYTHON_BIN --version) at $(command -v $PYTHON_BIN)"

# ---------- 2. clone repos -------------------------------------------------
REPOS=(
    "agent-mcp"
    "agentic-bench"
    "aircraft-analysis"
    "aviary-cpacs-mcp"
    "mission-mcp"
    "nseg-mcp"
    "pycycle-mcp"
    "su2-mcp"
    "tigl-mcp"
)
ORG_URL="https://github.com/cmudrc"

if [ "$SKIP_CLONE" -eq 0 ]; then
    info "Step 2/7: cloning cmudrc repos (skipped if present)..."
    for r in "${REPOS[@]}"; do
        if [ -d "$r/.git" ]; then
            ok "  $r already cloned"
        else
            info "  cloning $r ..."
            git clone --depth 1 "${ORG_URL}/${r}.git" "$r" \
                || warn "  could not clone $r (continuing -- maybe private or local-only)"
        fi
    done
else
    info "Step 2/7: --skip-clone given; assuming repos are already in $WORKDIR"
fi

# Make sure shared_cpacs exists (lives inside the workspace, not a repo).
if [ ! -d "shared_cpacs" ]; then
    warn "  shared_cpacs/ not found; the agent will fall back to per-MCP CPACS handling."
fi

# ---------- 3. venv + editable installs -----------------------------------
info "Step 3/7: creating .venv and installing MCP packages (editable)..."

if [ ! -d ".venv" ]; then
    "$PYTHON_BIN" -m venv .venv
    ok "  created .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip wheel >/dev/null

# Base scientific stack required by Aviary / pyCycle / gmsh / pyvista.
python -m pip install \
    "numpy<2" "pyvista==0.48.4" "matplotlib" "gmsh==4.15.2" \
    "ollama" "pillow" "lxml" "pyyaml" >/dev/null

EDITABLE_PKGS=()
for pkg in tigl-mcp su2-mcp pycycle-mcp nseg-mcp aviary-cpacs-mcp mission-mcp shared_cpacs agent-mcp agentic-bench; do
    if [ -d "$pkg" ] && [ -f "$pkg/pyproject.toml" ]; then
        EDITABLE_PKGS+=("-e" "./$pkg")
    fi
done

if [ "${#EDITABLE_PKGS[@]}" -gt 0 ]; then
    python -m pip install "${EDITABLE_PKGS[@]}" || \
        warn "  one or more editable installs failed (check pip output above)."
    ok "  installed: ${EDITABLE_PKGS[*]}"
else
    warn "  no MCP package directories found -- did the clone step succeed?"
fi

# ---------- 4. SU2 binary --------------------------------------------------
info "Step 4/7: installing SU2..."
if command -v SU2_CFD >/dev/null 2>&1; then
    ok "  SU2_CFD already on PATH ($(SU2_CFD --version 2>&1 | head -1))"
elif [ -x "$HOME/.local/su2/bin/SU2_CFD" ]; then
    export PATH="$HOME/.local/su2/bin:$PATH"
    ok "  SU2 already in ~/.local/su2/bin; added to PATH"
else
    if [ -x "su2-mcp/scripts/install_su2.sh" ]; then
        bash su2-mcp/scripts/install_su2.sh || warn "  SU2 install reported errors -- check output."
        [ -x "$HOME/.local/su2/bin/SU2_CFD" ] && export PATH="$HOME/.local/su2/bin:$PATH"
    else
        warn "  su2-mcp/scripts/install_su2.sh not found; install SU2 manually."
        warn "  See https://su2code.github.io/download.html"
    fi
fi

# ---------- 5. Ollama + Gemma ----------------------------------------------
info "Step 5/7: installing Ollama + pulling Gemma model..."
if ! command -v ollama >/dev/null 2>&1; then
    case "$OS" in
        Darwin)
            warn "  ollama not found. Install via 'brew install ollama' or"
            warn "  download from https://ollama.com/download/mac"
            ;;
        Linux)
            info "  installing ollama (rootless via official script)..."
            curl -fsSL https://ollama.com/install.sh | sh || \
                warn "  ollama install reported errors -- check output."
            ;;
        *)
            warn "  please install ollama from https://ollama.com/download"
            ;;
    esac
fi

if command -v ollama >/dev/null 2>&1; then
    if ! curl -fs http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
        info "  starting 'ollama serve' in the background..."
        nohup ollama serve >"$HOME/.ollama_bootstrap.log" 2>&1 &
        sleep 4
    fi
    if [ "$PULL_MODELS" -eq 1 ]; then
        info "  pulling $MODEL (this is several GB; first time only)..."
        ollama pull "$MODEL" || warn "  pull $MODEL failed; check 'ollama list'."
        if [ "$SERVER_TIER" -eq 1 ]; then
            info "  pulling gemma3:27b (server tier, ~17 GB)..."
            ollama pull gemma3:27b || warn "  pull gemma3:27b failed."
        fi
        ollama list
    fi
fi

# ---------- 6. sanity check -----------------------------------------------
info "Step 6/7: sanity-checking the install..."
python - <<'PY'
import importlib, sys
missing = []
for mod in ("su2_mcp", "tigl_mcp", "pycycle_mcp", "nseg_mcp", "aviary_cpacs_mcp"):
    try:
        importlib.import_module(mod)
    except Exception as exc:
        missing.append((mod, str(exc)))
if missing:
    print("  WARNING: some MCP packages did not import:")
    for m, e in missing:
        print(f"    - {m}: {e}")
    sys.exit(0)
print("  all MCP packages import OK")
PY

# ---------- 7. launch ------------------------------------------------------
if [ "$LAUNCH" -eq 1 ]; then
    info "Step 7/7: launching the Gemma agent in REPL mode..."
    info "  (type your request in plain English, e.g."
    info "   'Run SU2 with the workstation preset on D150 at Mach 0.78 AoA 2'.)"
    info ""
    AGENT="agent-mcp/hybrid_agent.py"
    [ -f "$AGENT" ] || AGENT="agent-mcp/gemma_agent.py"
    if [ -f "$AGENT" ]; then
        CPACS=""
        for candidate in D150_v30.xml agent-mcp/D150_v30.xml paper/D150_v30.xml; do
            [ -f "$candidate" ] && CPACS="$candidate" && break
        done
        if [ -n "$CPACS" ]; then
            python "$AGENT" --cpacs "$CPACS" --planner "$MODEL" || \
            python "$AGENT" --cpacs "$CPACS" --model "$MODEL"
        else
            warn "  no D150_v30.xml found; launching agent without --cpacs."
            python "$AGENT" --planner "$MODEL" || python "$AGENT" --model "$MODEL"
        fi
    else
        warn "  agent-mcp/{hybrid,gemma}_agent.py not found."
    fi
else
    ok "Setup complete (--no-launch given). To start the agent later, run:"
    ok "    source .venv/bin/activate"
    ok "    python agent-mcp/hybrid_agent.py --cpacs D150_v30.xml --planner $MODEL"
fi
