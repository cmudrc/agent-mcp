# bootstrap.ps1 -- One-command setup for the CMU DRC MCP pipeline on Windows.
#
# Windows is fully supported for the Python/pyCycle/Aviary side of the
# pipeline. SU2 itself ships pre-built binaries only for Linux/macOS, so
# this script will:
#   1. install Python deps + every MCP package (editable) on native Windows,
#   2. install Ollama (winget if available) and pull the Gemma model,
#   3. for SU2 fall back to WSL2 (the script will hand off to the bash
#      bootstrap if WSL is installed), or print clear instructions to
#      install WSL2 if it isn't.
#
# Usage (from PowerShell in the project root):
#     pwsh -ExecutionPolicy Bypass -File .\agent-mcp\bootstrap.ps1
#
# Flags mirror bootstrap.sh: -NoLaunch, -NoModels, -ServerTier,
# -Model <name>, -WorkDir <dir>, -SkipClone.
[CmdletBinding()]
param(
    [switch]$NoLaunch,
    [switch]$NoModels,
    [switch]$ServerTier,
    [switch]$SkipClone,
    [string]$Model = "gemma4:e4b",
    [string]$WorkDir = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

function Info($msg)  { Write-Host "[bootstrap] $msg" -ForegroundColor Blue }
function OK($msg)    { Write-Host "[bootstrap] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "[bootstrap] $msg" -ForegroundColor Yellow }
function Die($msg)   { Write-Host "[bootstrap] $msg" -ForegroundColor Red; exit 1 }

Set-Location $WorkDir

# ---------- 1. prerequisites -----------------------------------------------
Info "Step 1/7: checking prerequisites..."
foreach ($cmd in @("git", "curl")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Die "Missing required command: $cmd. Install via 'winget install $cmd' or https://git-scm.com/download/win"
    }
}

$python = $null
foreach ($candidate in @("python3.13", "python3.12", "python")) {
    $exe = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($exe) {
        $ver = (& $exe.Source -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
        if ($ver -eq "3.12" -or $ver -eq "3.13") {
            $python = $exe.Source
            break
        }
    }
}
if (-not $python) {
    Die "Need Python 3.12 or 3.13 on PATH. Install from https://python.org or 'winget install Python.Python.3.12'"
}
OK "  python: $((& $python --version)) at $python"

# ---------- 2. clone repos -------------------------------------------------
$repos = @(
    "agent-mcp", "agentic-bench", "aircraft-analysis", "aviary-cpacs-mcp",
    "mission-mcp", "nseg-mcp", "pycycle-mcp", "su2-mcp", "tigl-mcp"
)
if (-not $SkipClone) {
    Info "Step 2/7: cloning cmudrc repos..."
    foreach ($r in $repos) {
        if (Test-Path (Join-Path $r ".git")) {
            OK "  $r already cloned"
        } else {
            Info "  cloning $r ..."
            try {
                git clone --depth 1 "https://github.com/cmudrc/$r.git" $r
            } catch {
                Warn "  could not clone $r ($_) -- continuing"
            }
        }
    }
} else {
    Info "Step 2/7: -SkipClone given; assuming repos already in $WorkDir"
}

# ---------- 3. venv + editable installs -----------------------------------
Info "Step 3/7: creating .venv and installing MCP packages..."
if (-not (Test-Path ".venv")) {
    & $python -m venv .venv
    OK "  created .venv"
}
$venvPy = Join-Path ".venv" "Scripts\python.exe"
& $venvPy -m pip install --upgrade pip wheel | Out-Null
& $venvPy -m pip install "numpy<2" "pyvista==0.48.4" "matplotlib" "gmsh==4.15.2" `
    "ollama" "pillow" "lxml" "pyyaml" | Out-Null

$editable = @()
foreach ($pkg in @("tigl-mcp", "su2-mcp", "pycycle-mcp", "nseg-mcp",
                   "aviary-cpacs-mcp", "mission-mcp", "shared_cpacs",
                   "agent-mcp", "agentic-bench")) {
    if (Test-Path (Join-Path $pkg "pyproject.toml")) {
        $editable += @("-e", ".\$pkg")
    }
}
if ($editable.Count -gt 0) {
    & $venvPy -m pip install @editable
    OK "  installed: $($editable -join ' ')"
}

# ---------- 4. SU2 binary --------------------------------------------------
Info "Step 4/7: installing SU2..."
if (Get-Command SU2_CFD -ErrorAction SilentlyContinue) {
    OK "  SU2_CFD already on PATH"
} elseif (Get-Command wsl -ErrorAction SilentlyContinue) {
    Warn "  SU2 binaries are not provided for native Windows."
    Warn "  Detected WSL2. Inside WSL run:"
    Warn "    cd $(wsl wslpath -a $WorkDir.Replace('\','/'))"
    Warn "    bash agent-mcp/bootstrap.sh --no-launch"
    Warn "  Then go back to PowerShell and re-run this script with -NoLaunch."
} else {
    Warn "  No WSL detected. Install WSL2 (one command, as Administrator):"
    Warn "    wsl --install"
    Warn "  Then re-run this script."
}

# ---------- 5. Ollama + Gemma ----------------------------------------------
Info "Step 5/7: installing Ollama + pulling Gemma model..."
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Ollama.Ollama --silent --accept-source-agreements --accept-package-agreements
    } else {
        Warn "  Install Ollama manually from https://ollama.com/download/windows"
    }
}
if ((Get-Command ollama -ErrorAction SilentlyContinue) -and -not $NoModels) {
    Info "  pulling $Model ..."
    ollama pull $Model
    if ($ServerTier) {
        Info "  pulling gemma3:27b ..."
        ollama pull gemma3:27b
    }
    ollama list
}

# ---------- 6. sanity check ------------------------------------------------
Info "Step 6/7: sanity-checking the install..."
& $venvPy -c "import su2_mcp, tigl_mcp, pycycle_mcp, nseg_mcp, aviary_cpacs_mcp; print('all MCP packages import OK')"

# ---------- 7. launch ------------------------------------------------------
if (-not $NoLaunch) {
    Info "Step 7/7: launching the Gemma agent..."
    $agent = "agent-mcp\hybrid_agent.py"
    if (-not (Test-Path $agent)) { $agent = "agent-mcp\gemma_agent.py" }
    $cpacs = $null
    foreach ($c in @("D150_v30.xml", "agent-mcp\D150_v30.xml", "paper\D150_v30.xml")) {
        if (Test-Path $c) { $cpacs = $c; break }
    }
    if ($cpacs) {
        & $venvPy $agent --cpacs $cpacs --planner $Model
    } else {
        & $venvPy $agent --planner $Model
    }
} else {
    OK "Setup complete (-NoLaunch given). To start the agent later, run:"
    OK "    .\.venv\Scripts\Activate.ps1"
    OK "    python agent-mcp\hybrid_agent.py --cpacs D150_v30.xml --planner $Model"
}
