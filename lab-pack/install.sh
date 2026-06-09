#!/usr/bin/env bash
# Caliper Lab Pack — one-command install on a lab server (no root required).
#
# Installs the full bioinformatics toolset + the lab runner into a confined workspace,
# then prints the connection config to register the lab with the control plane.
# Prefers a reproducible container (Apptainer/Singularity); falls back to a pinned
# conda environment where no container runtime is available.
#
#   curl -fsSL https://get.caliper.morphmind.ai/install.sh | bash
#
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="${CALIPER_WORKSPACE:-$HOME/caliper}"
mkdir -p "$WORKSPACE"; cd "$WORKSPACE"
echo "Installing the Caliper Lab Pack into $WORKSPACE …"

EXEC=""
RUNTIME="$(command -v apptainer || command -v singularity || true)"
if [ -n "$RUNTIME" ]; then
  echo "Container runtime found ($RUNTIME) — pulling the reproducible image…"
  if "$RUNTIME" pull caliper-lab.sif "${CALIPER_IMAGE:-oras://ghcr.io/aiscientists-dev/caliper-lab:latest}"; then
    EXEC="$RUNTIME exec --bind $WORKSPACE caliper-lab.sif python"
  else
    echo "  image pull failed — falling back to conda."
  fi
fi

if [ -z "$EXEC" ]; then
  echo "Using a pinned conda environment (no container runtime)…"
  MM="$(command -v mamba || command -v micromamba || true)"
  if [ -z "$MM" ]; then
    echo "  fetching micromamba…"
    curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj bin/micromamba >/dev/null
    MM="$WORKSPACE/bin/micromamba"
  fi
  "$MM" create -y -p "$WORKSPACE/env/bio" -f "$HERE/environment.yml"
  cp "$HERE/../caliper/lab/runner.py" "$WORKSPACE/env/bio/runner.py" 2>/dev/null || true
  EXEC="$WORKSPACE/env/bio/bin/python"
fi

cat <<CONF

=== Lab Pack installed. Register this lab with the control plane ===
  CALIPER_REMOTE_HOST=$(hostname -I 2>/dev/null | awk '{print $1}')
  CALIPER_REMOTE_WORKSPACE=$WORKSPACE
  CALIPER_REMOTE_EXEC=$EXEC
The control plane connects over SSH and writes ONLY under $WORKSPACE;
your data is read in place and never leaves this server.
CONF
