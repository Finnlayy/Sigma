#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "$script_dir/.." && pwd)"
output_path="${1:-$repository_root/claude-desktop_1.52386.6_amd64.deb}"
part_prefix="$repository_root/claude-desktop_1.52386.6_amd64.deb.part-"

cat "${part_prefix}"* > "$output_path"
expected_sha256="2e83a76c6ed9187671bfe80664fc6d59840171f4a2a81f408662c879a67f4e0a"
actual_sha256="$(sha256sum "$output_path" | awk '{print $1}')"

if [[ "$actual_sha256" != "$expected_sha256" ]]; then
    printf 'checksum mismatch: %s\n' "$output_path" >&2
    rm -f "$output_path"
    exit 1
fi

printf 'reassembled and verified: %s\n' "$output_path"