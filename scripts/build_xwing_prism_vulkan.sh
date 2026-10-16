#!/usr/bin/env bash
# Build PrismML PTQ1_0 Vulkan binaries; never touches a live service.
set -euo pipefail
SRC="${PRISM_SOURCE:-/home/scott/llama.cpp-prism}"
OUT="${PRISM_BUILD_DIR:-$SRC/build-vulkan-gfx1151-20261016}"
JOBS="${PRISM_BUILD_JOBS:-3}"
[[ -d "$SRC/ggml/src/ggml-vulkan" ]] || { echo 'PrismML source missing' >&2; exit 2; }
[[ "$JOBS" =~ ^[1-3]$ ]] || { echo 'JOBS must be 1-3' >&2; exit 3; }
read -r available < <(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
(( available > 16 * 1024 * 1024 )) || { echo 'Insufficient available RAM (<16 GiB)' >&2; exit 4; }
command -v cmake >/dev/null
command -v ninja >/dev/null
GLSLC="$(command -v glslc)"
mkdir -p "$OUT"
WRAPPER="$OUT/glslc-target-vulkan13.sh"
if [[ ! -e "$WRAPPER" ]]; then
  cat > "$WRAPPER" <<'WRAP'
#!/usr/bin/env bash
set -euo pipefail
args=()
for arg in "$@"; do
  [[ "$arg" != '--target-env=vulkan1.2' ]] || arg='--target-env=vulkan1.3'
  args+=("$arg")
done
exec /usr/local/bin/glslc "${args[@]}"
WRAP
  chmod 700 "$WRAPPER"
fi
nice -n 10 cmake -S "$SRC" -B "$OUT" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DGGML_VULKAN=ON -DGGML_CUDA=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_CURL=OFF \
  -DVulkan_GLSLC_EXECUTABLE="$WRAPPER"
nice -n 10 timeout 240 cmake --build "$OUT" --target llama-bench llama-server -j "$JOBS"
printf 'Built PrismML %s in %s\n' "$(git -C "$SRC" rev-parse --short HEAD)" "$OUT/bin"
