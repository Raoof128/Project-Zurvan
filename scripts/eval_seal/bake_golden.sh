#!/bin/bash
# Bake the Z1b GOLDEN guest image (design §7j/§7l/§7m), reproducibly, as the DEV user (no sudo).
#
#   scripts/eval_seal/bake_golden.sh [OUT_DIR]        (default OUT_DIR: ~/.zurvan/z1b)
#
# Output: OUT_DIR/guest.qcow2 + OUT_DIR/guest.qcow2.sha512. The printed digest must then be pinned as
# vm_runner.GOLDEN_IMAGE_DIGEST in a reviewed commit; install.sh --guest-image refuses any other image.
#
# What the broker needs from the image (broker.py / vm_runner.py are the authority):
#   /opt/z1b/runtime/bin/python3   a --copies venv: torch (CPU wheel) + sentence-transformers + sqlite-vec
#   /opt/z1b/runtime/hf            the MiniLM HF cache, offline (copied from the host cache that built the index)
#   /opt/z1b/scripts/...           this commit's scripts/ tree (broker_provision_corpus imports
#                                  scripts.eval_seal.corpus from /opt/z1b; the candidate itself is pushed per run)
#   squashfs-tools, iptables, sudo, a loop-capable kernel (Debian 12 stock)
#
# Versions are pinned to the host stack that builds the production search index, so the query embedding
# in-guest and the document embeddings on the host come from the same code and the same model snapshot.
# ONE deviation: sqlite-vec 0.1.7, not the host's 0.1.6 -- the 0.1.6 manylinux aarch64 wheel ships a 32-bit
# ARM vec0.so ("wrong ELF class: ELFCLASS32", found 2026-11-08). Zurvan only calls the scalar
# vec_distance_cosine() over a full scan (vec_storage.py: never the vec0 KNN path).
#
# Hygiene: the bake runs as its own guest user (z1bbake). Before the disk is frozen, that user, its ssh key
# and every cloud-init sudoers grant are removed and cloud-init is reset, so a VM booted from the image
# authorizes ONLY the key Lima injects for that run -- never the dev-side bake key.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-$HOME/.zurvan/z1b}"
HF_MODEL_DIR="${ZURVAN_Z1B_HF_MODEL_DIR:-$HOME/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2}"
INST="z1bbake"
BAKE_USER="z1bbake"
export LIMA_HOME="$OUT/bake-lima"

# Debian 12 (bookworm) genericcloud arm64, pinned exactly as Lima 2.3.0's template pins it.
BASE_URL="https://cloud.debian.org/images/cloud/bookworm/20261026-2643/debian-12-genericcloud-arm64-20261026-2643.qcow2"
BASE_DIGEST="sha512:f42866fe22b4baf9f0ed4f81d620064f2a4b86b18b92bfe3aaaa40babf6819d39cbf0caaaad13f4ae3e7c9f82cbd78b0c8cabadd8bc737ce13578e61fa86970c"

TORCH_INDEX="https://download.pytorch.org/whl/cpu"
TORCH_PIN="torch==2.8.0"
PIP_PINS="sentence-transformers==5.2.0 transformers==4.46.3 tokenizers==0.20.3 huggingface-hub==0.35.0 \
safetensors==0.6.2 numpy==1.26.4 sqlite-vec==0.1.7 scikit-learn==1.4.0 scipy==1.16.1"

die() { echo "bake_golden: $*" >&2; exit 1; }

command -v limactl >/dev/null || die "limactl not found"
command -v qemu-img >/dev/null || die "qemu-img not found"
[ -d "$HF_MODEL_DIR/snapshots" ] || die "no MiniLM HF cache at $HF_MODEL_DIR"
[ -z "$(git -C "$REPO" status --porcelain -- scripts)" ] || die "scripts/ has uncommitted changes; bake from a clean commit"
[ ! -e "$OUT/guest.qcow2" ] || die "$OUT/guest.qcow2 already exists; move it aside first"
mkdir -p "$OUT"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/z1bbake.XXXXXX")"
cleanup() {
  limactl stop -f "$INST" >/dev/null 2>&1 || true
  limactl delete -f "$INST" >/dev/null 2>&1 || true
  rm -rf "$WORK" "$LIMA_HOME"
}
trap cleanup EXIT

COMMIT="$(git -C "$REPO" rev-parse HEAD)"
echo "bake_golden: commit $COMMIT"

cat > "$WORK/bake.yaml" <<EOF
images:
- location: "$BASE_URL"
  arch: "aarch64"
  digest: "$BASE_DIGEST"
mounts: []
containerd:
  system: false
  user: false
cpus: 4
memory: "6GiB"
disk: "20GiB"
user:
  name: "$BAKE_USER"
EOF

limactl delete -f "$INST" >/dev/null 2>&1 || true
limactl start --name="$INST" --tty=false "$WORK/bake.yaml"

g() { limactl shell --workdir / "$INST" "$@"; }

echo "bake_golden: apt"
g sudo env DEBIAN_FRONTEND=noninteractive sh -c \
  "apt-get update -q && apt-get install -y -q --no-install-recommends python3 python3-venv squashfs-tools iptables sudo ca-certificates"

echo "bake_golden: runtime venv"
g sudo python3 -m venv --copies /opt/z1b/runtime
# torch alone from the CPU index (no CUDA payload), WITHOUT its deps: that index carries some of them as sdists
# only. The second install resolves torch's deps from the installed CPU wheel's own metadata, wheels only.
g sudo /opt/z1b/runtime/bin/pip install -q --no-cache-dir --only-binary=:all: --no-deps --index-url "$TORCH_INDEX" "$TORCH_PIN"
# shellcheck disable=SC2086
g sudo /opt/z1b/runtime/bin/pip install -q --no-cache-dir --only-binary=:all: $PIP_PINS "$TORCH_PIN"
g sudo /opt/z1b/runtime/bin/pip check

echo "bake_golden: model cache + scripts tree"
tar -C "$(dirname "$HF_MODEL_DIR")" -cf "$WORK/hf.tar" "$(basename "$HF_MODEL_DIR")"
git -C "$REPO" archive --format=tar "$COMMIT" scripts > "$WORK/scripts.tar"
limactl copy "$WORK/hf.tar" "$INST:/tmp/hf.tar"
limactl copy "$WORK/scripts.tar" "$INST:/tmp/scripts.tar"
g sudo sh -c "set -e; mkdir -p /opt/z1b/runtime/hf/hub; tar xf /tmp/hf.tar -C /opt/z1b/runtime/hf/hub; \
  tar xf /tmp/scripts.tar -C /opt/z1b; rm -f /tmp/hf.tar /tmp/scripts.tar; \
  printf '%s\n' '$COMMIT' > /opt/z1b/BAKED_FROM; chown -R root:root /opt/z1b; chmod -R a+rX,go-w /opt/z1b"

echo "bake_golden: in-guest acceptance"
g sudo env -i HF_HUB_OFFLINE=1 HF_HOME=/opt/z1b/runtime/hf /opt/z1b/runtime/bin/python3 -B -E -s -c '
import sys
sys.path.insert(0, "/opt/z1b")
assert sys.prefix == "/opt/z1b/runtime", sys.prefix
import sqlite3, struct, torch, sqlite_vec, numpy
from sentence_transformers import SentenceTransformer
c = sqlite3.connect(":memory:"); c.enable_load_extension(True); sqlite_vec.load(c)
d = c.execute("select vec_distance_cosine(?, ?)", (struct.pack("2f", 1, 0), struct.pack("2f", 0, 1))).fetchone()[0]
assert abs(d - 1.0) < 1e-6, d
from scripts.eval_seal.corpus import corpus_sha256
assert not torch.cuda.is_available()
v = SentenceTransformer("all-MiniLM-L6-v2").encode("frobnicator widget")
assert len(v) == 384, len(v)
print("GOLDEN_OK torch=%s sqlite_vec=%s dim=%d" % (torch.__version__, c.execute("select vec_version()").fetchone()[0], len(v)))
'
g sh -c 'command -v mksquashfs && command -v iptables && command -v ip6tables'

echo "bake_golden: hygiene + freeze"
g sudo sh -c "apt-get clean; rm -rf /var/lib/apt/lists/* /root/.cache /tmp/* /var/tmp/*; \
  cloud-init clean --logs; rm -f /etc/sudoers.d/90-cloud-init-users /etc/sudoers.d/*lima* /root/.ssh/authorized_keys; \
  fstrim -av || true; \
  nohup sh -c 'sleep 2; pkill -KILL -u $BAKE_USER; userdel -r -f $BAKE_USER; sync; poweroff' >/dev/null 2>&1 &"
for _ in $(seq 1 60); do
  [ "$(limactl list --format '{{.Status}}' "$INST" 2>/dev/null)" = "Stopped" ] && break
  sleep 2
done
limactl stop -f "$INST" >/dev/null 2>&1 || true

DISK="$LIMA_HOME/$INST/disk"
[ -f "$DISK" ] || DISK="$LIMA_HOME/$INST/diffdisk"
[ -f "$DISK" ] || die "no instance disk under $LIMA_HOME/$INST"
qemu-img convert -O qcow2 "$DISK" "$OUT/guest.qcow2.partial"
mv "$OUT/guest.qcow2.partial" "$OUT/guest.qcow2"
chmod 0444 "$OUT/guest.qcow2"
DIG="$(shasum -a 512 "$OUT/guest.qcow2" | cut -d' ' -f1)"
printf '%s\n' "$DIG" > "$OUT/guest.qcow2.sha512"
echo "bake_golden: DONE $(du -h "$OUT/guest.qcow2" | cut -f1) sha512:$DIG (baked from $COMMIT)"
