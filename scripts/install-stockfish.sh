#!/bin/sh
# Install the pinned official Stockfish release for Linux, verified by SHA-256 (ADR-023).
#
#   scripts/install-stockfish.sh <destination-binary-path>
#
# Used by the Dockerfile and by CI, so both run exactly the same engine build. The
# checksums come from the release's published asset digests; a download that does not
# match is rejected. On macOS, install the same version with `brew install stockfish`.
set -eu

VERSION="19"
SHA256_X86_64="9defc0d4e55d49c65a6d042f3e571a39fcea499ade6dbe741b53b8c65e03611f"
SHA256_ARM64="fe26cfd1d9db4c8af3d21e24d9ff34cacb31c1f940085a7583da11796f2bac01"

dest="${1:?usage: install-stockfish.sh <destination-binary-path>}"

case "$(uname -m)" in
  x86_64 | amd64) arch="x86-64"; sha256="$SHA256_X86_64" ;;
  aarch64 | arm64) arch="arm64"; sha256="$SHA256_ARM64" ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

name="stockfish-linux-${arch}-universal"
url="https://github.com/official-stockfish/Stockfish/releases/download/sf_${VERSION}/${name}.tar.gz"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "downloading Stockfish ${VERSION} (${arch})"
curl --fail --silent --show-error --location --retry 3 --output "$work/sf.tar.gz" "$url"
echo "${sha256}  $work/sf.tar.gz" | sha256sum -c -
tar -xzf "$work/sf.tar.gz" -C "$work" "stockfish/${name}"
install -D -m 0755 "$work/stockfish/${name}" "$dest"
echo "installed $("$dest" quit | head -n 1)"
