#!/usr/bin/env bash
set -euo pipefail
test "$(uname -m)" = aarch64
command -v nvidia-smi
nvidia-smi
sudo apt-get update
sudo apt-get install -y git curl openssh-server python3 python3-yaml netplan.io iproute2 iputils-ping rdma-core ibverbs-utils perftest cockpit unzip
command -v docker
command -v nvidia-ctk
sudo systemctl enable --now ssh docker cockpit.socket
sudo docker info >/dev/null
printf '\n基础软件检查完成。请继续执行主节点的网络配置步骤。\n'
