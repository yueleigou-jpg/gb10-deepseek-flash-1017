#!/usr/bin/env bash
set -euo pipefail
test "$(uname -m)" = aarch64
root="$HOME/spark-deploy"
cache="$root/.work/runtime"
mkdir -p "$cache" "$root/data/nccl-ring" "$root/data/cache"
cd "$cache"
archive=native-runtime-files-20260908.tar
url=https://github.com/FujitsuPolycom/sparkring/releases/download/native-runtime-sm121-aa8fa11831af
curl -fL --retry 3 -C - -o "$archive" "$url/$archive"
echo "aa8fa11831afaa4539e0b74442fd1ea25dd8cf49ad5edb6fd95e7f05fdf5ce86  $archive" | sha256sum -c -
tar -xf "$archive" native/libnccl.so.2.30.7
echo '768a450b5eb84bf3d1191795350e43c96de75aeba4783ec314d47672fe6e1fc6  native/libnccl.so.2.30.7' | sha256sum -c -
install -m 755 native/libnccl.so.2.30.7 "$root/data/nccl-ring/"
rust=rust-1.92.0-aarch64-unknown-linux-gnu
curl -fLO --retry 3 "https://static.rust-lang.org/dist/$rust.tar.xz"
curl -fLO --retry 3 "https://static.rust-lang.org/dist/$rust.tar.xz.sha256"
sha256sum -c "$rust.tar.xz.sha256"
tar -xf "$rust.tar.xz"
bash "$rust/install.sh" --prefix="$root/data/cache/rust-1.92.0-aarch64" --disable-ldconfig
