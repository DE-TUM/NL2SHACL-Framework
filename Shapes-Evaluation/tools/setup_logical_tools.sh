#!/usr/bin/env bash
# setup_logical_tools.sh
# ----------------------
# Installs the tools needed by the logical evaluator (LER) into
# Shapes-Evaluation/tools/bin/:
#   SHACL2FOL.jar  translates SHACL shapes graphs into first-order logic
#   vampire        the Vampire 4.9 theorem prover (Linux x86-64)
#
# Both files are taken from the SHACL2FOL repository (Apache-2.0,
# https://github.com/paolo7/SHACL2FOL) at a fixed commit and checked against
# their SHA-256 checksums, so everybody evaluates with the same versions.
# The download is about 46 MB.
#
# Usage:
#   bash tools/setup_logical_tools.sh                 # download
#   bash tools/setup_logical_tools.sh --from DIR      # copy SHACL2FOL.jar and vampire from DIR
#                                                     # (for machines without internet access)
#   bash tools/setup_logical_tools.sh --dry-run       # only show what would be downloaded
#
# SHACL2FOL needs Java 21 or newer, which is not installed by this script.
# For example with conda:  conda create -n shacl2fol -c conda-forge openjdk=21
# Then set JAVA_BIN to that java, or put it first on PATH.

set -euo pipefail

COMMIT="ab2c7e801cd0df07d753cb6a5685bceffeb1e8fc"
BASE_URL="https://github.com/paolo7/SHACL2FOL/raw/${COMMIT}/runnable"
JAR_SHA256="2fcb6f01dcf96a2db7fe000c2aaf523ee3fb5800c5daf119faa3ac70627f218f"
VAMPIRE_SHA256="ce3b39047565a3980e3d490315ff129f0eca5f8fbd40eeb0d6850122eb295b54"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${HERE}/bin"
FROM_DIR=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)    FROM_DIR="${2:?--from needs a directory}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

fetch() {   # fetch <file name> <expected sha256>
    local name="$1" expected="$2" target="${DEST}/$1"
    if [[ -f "$target" && "$(sha256_of "$target")" == "$expected" ]]; then
        echo "  ${name}: already installed"
        return
    fi
    if [[ -n "$FROM_DIR" ]]; then
        [[ -f "${FROM_DIR}/${name}" ]] || { echo "  ${name}: not found in ${FROM_DIR}" >&2; exit 1; }
        cp "${FROM_DIR}/${name}" "${target}.part"
    else
        echo "  ${name}: downloading from ${BASE_URL}/${name}"
        curl -fL --retry 3 --progress-bar -o "${target}.part" "${BASE_URL}/${name}"
    fi
    local actual
    actual="$(sha256_of "${target}.part")"
    if [[ "$actual" != "$expected" ]]; then
        rm -f "${target}.part"
        echo "  ${name}: checksum mismatch (expected ${expected}, got ${actual})" >&2
        exit 1
    fi
    mv "${target}.part" "$target"
    echo "  ${name}: installed"
}

echo "SHACL2FOL commit: ${COMMIT}"
echo "Install directory: ${DEST}"

if [[ "$DRY_RUN" == 1 ]]; then
    for name in SHACL2FOL.jar vampire; do
        status="$(curl -sIL "${BASE_URL}/${name}" | grep -i -E '^HTTP|^content-length' | tail -2 | tr '\r\n' '  ')"
        echo "  ${BASE_URL}/${name}  ->  ${status}"
    done
    exit 0
fi

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo "Warning: the bundled Vampire binary is for Linux x86-64; on this system ($(uname -s) $(uname -m))" >&2
    echo "         install Vampire yourself and point VAMPIRE_PATH to it." >&2
fi

mkdir -p "$DEST"
fetch SHACL2FOL.jar "$JAR_SHA256"
fetch vampire "$VAMPIRE_SHA256"
chmod +x "${DEST}/vampire"

echo
echo "Checking the installation ..."
cd "${HERE}/.."
python3 - <<'EOF' || true
import sys
sys.path.insert(0, ".")
from logical_equivalence import resolve_tools
tools, problem = resolve_tools()
if tools:
    print("  OK: java=%s" % tools["java"])
    print("      jar=%s" % tools["jar"])
    print("      prover=%s" % tools["prover"])
else:
    print("  Not ready yet: %s" % problem)
EOF
