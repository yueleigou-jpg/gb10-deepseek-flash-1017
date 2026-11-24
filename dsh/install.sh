#!/usr/bin/env bash
set -euo pipefail
umask 077
root="$HOME/spark-deploy"
cd "$root/dsh"
test "$(uname -m)" = aarch64
test -s "$root/state/backend.env"
test ! -e home/settings.yaml
mkdir -p package/app home workspace "$HOME/.config/systemd/user"
release=node-v24.14.0-linux-arm64
base=https://nodejs.org/dist/v24.14.0
cd package
curl -fLO --retry 3 "$base/$release.tar.xz"
curl -fLsS "$base/SHASUMS256.txt" -o SHA256SUMS
grep " $release.tar.xz$" SHA256SUMS | sha256sum -c -
tar -xf "$release.tar.xz"
export PATH="$PWD/$release/bin:$PATH"
cp ../package.json ../package-lock.json app/
npm ci --prefix "$PWD/app" --no-audit --no-fund
cd ..
install -m 600 settings.yaml home/settings.yaml
install -m 644 gb10-dsh.service "$HOME/.config/systemd/user/"
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now gb10-dsh.service
