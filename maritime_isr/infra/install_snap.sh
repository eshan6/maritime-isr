#!/usr/bin/env bash
# Unit 0.2 — ESA SNAP native install + memory config for the 24 GB Oracle VM.
#
# SNAP preprocessing is a BATCH step (fires when a SAR scene lands every 2-4
# days), separate from the live AIS stream. Native install keeps `gpt` on PATH,
# lets pyroSAR find it, and persists orbit/DEM caches in ~/.snap across runs.
#
# Run once on the VM:  bash install_snap.sh
# Idempotent-ish: re-running re-applies the memory config; skips download if the
# installer is already present.
set -euo pipefail

SNAP_VERSION="${SNAP_VERSION:-10.0.0}"
INSTALLER="esa-snap_all_linux-${SNAP_VERSION}.sh"
# ESA moved hosting to download.esa.int; the 'all' bundle includes s1tbx.
URL="https://download.esa.int/step/snap/${SNAP_VERSION%.*}/installers/${INSTALLER}"
SNAP_DIR="${SNAP_DIR:-$HOME/esa-snap}"

echo "==> SNAP ${SNAP_VERSION} native install"

# --- prerequisites ---------------------------------------------------------
if ! command -v java >/dev/null 2>&1; then
  echo "==> installing OpenJDK 11 (SNAP 10 bundles its own JRE, but keep one for tools)"
  sudo apt-get update -y && sudo apt-get install -y default-jre-headless wget
fi

# --- download --------------------------------------------------------------
cd "$HOME"
if [ ! -f "$INSTALLER" ]; then
  echo "==> downloading $URL"
  wget -q --show-progress "$URL" -O "$INSTALLER" || {
    echo "!! download failed. Check the version/URL at https://step.esa.int/main/download/snap/"
    echo "   Set SNAP_VERSION env var to a currently-hosted version and re-run."
    exit 1
  }
fi
chmod +x "$INSTALLER"

# --- unattended install ----------------------------------------------------
# The installer is a Bitrock/VarFile installer; drive it non-interactively.
cat > /tmp/snap_response.varfile <<VARS
sys.installationDir=$SNAP_DIR
sys.languageId=en
sys.programGroupDisabled\$Boolean=true
executeLauncherWithGuiArguments\$Boolean=false
forcePython\$Boolean=false
VARS

echo "==> installing to $SNAP_DIR (unattended)"
"./$INSTALLER" -q -varfile /tmp/snap_response.varfile

# --- PATH ------------------------------------------------------------------
GPT="$SNAP_DIR/bin/gpt"
if [ ! -x "$GPT" ]; then
  echo "!! gpt not found at $GPT — install may have used a different dir."
  echo "   Find it with:  find \$HOME -name gpt -type f 2>/dev/null"
  exit 1
fi
if ! grep -q "esa-snap/bin" "$HOME/.bashrc" 2>/dev/null; then
  echo "export PATH=\"$SNAP_DIR/bin:\$PATH\"" >> "$HOME/.bashrc"
  echo "==> added $SNAP_DIR/bin to PATH in ~/.bashrc"
fi

# --- MEMORY CAPS for 24 GB VM (the make-or-break part) ---------------------
# gpt's JVM will grab most of RAM and thrash without this. Cap heap well under
# 24 GB to leave room for the OS, the AIS consumer, DuckDB, and OS file cache.
GPT_VMOPTIONS="$SNAP_DIR/bin/gpt.vmoptions"
echo "==> writing memory caps to $GPT_VMOPTIONS"
cat > "$GPT_VMOPTIONS" <<VMOPTS
-Xms2G
-Xmx12G
-XX:+UseParallelGC
-Dsnap.jai.tileCacheSize=4096
-Dsnap.parallelism=4
VMOPTS

# SNAP user config: tile cache + parallelism (belt-and-suspenders vs vmoptions)
SNAP_CONF_DIR="$HOME/.snap/etc"
mkdir -p "$SNAP_CONF_DIR"
cat > "$SNAP_CONF_DIR/snap.properties" <<PROPS
snap.jai.tileCacheSize=4096
snap.jai.defaultTileSize=512
snap.parallelism=4
snap.dataio.reader.tileWidth=512
snap.dataio.reader.tileHeight=512
PROPS

# --- update SNAP modules (optional but avoids known GRD bugs) ---------------
echo "==> updating SNAP modules (may take a few min; safe to skip with Ctrl-C)"
"$GPT" --diag >/dev/null 2>&1 || true

echo ""
echo "==> DONE. Verify with:"
echo "     source ~/.bashrc && gpt --diag"
echo "   Then in the repo:  pip install -e '.[sar]'"
echo "   Test one scene:    maritime-isr preprocess --limit 1"
