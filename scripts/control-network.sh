#!/usr/bin/env bash
set -euo pipefail
bridge=sparkctrl0
alias=spark-1017-control-vxlan
test "$EUID" = 0
config=/etc/spark-cluster-control.conf
test "$(stat -c %u "$config")" = 0
test -z "$(find "$config" -perm /022 -print)"
source "$config"
test "${#links[@]}" -ge 1
if [[ ${1:-} == remove ]]; then
    test "$(cat /sys/class/net/"$bridge"/ifalias)" = "$alias"
    for link in "${links[@]}"; do
        read -r id local_ip remote_ip port <<< "$link"
        if [[ -e /sys/class/net/svx$id ]]; then
            test "$(cat "/sys/class/net/svx$id/ifalias")" = "$alias"
            ip link del "svx$id"
        fi
    done
    ip link del "$bridge"
    exit
fi
test "${1:-}" = apply
if [[ -e /sys/class/net/$bridge ]]; then
    test "$(cat /sys/class/net/"$bridge"/ifalias)" = "$alias"
    ip -4 -o address show dev "$bridge" | grep -Fq "$control_cidr "
    for link in "${links[@]}"; do
        read -r id local_ip remote_ip port <<< "$link"
        test "$(cat "/sys/class/net/svx$id/ifalias")" = "$alias"
        test "$(basename "$(readlink -f "/sys/class/net/svx$id/master")")" = "$bridge"
    done
    exit 0
fi
[[ ! -e /sys/class/net/$bridge ]]
test -z "$(ip -4 route show table all exact "$control_network")"
for link in "${links[@]}"; do
    read -r id local_ip remote_ip port <<< "$link"
    [[ ! -e /sys/class/net/svx$id ]]
    ip -4 route get "$remote_ip" from "$local_ip" | grep -Fq "dev $port"
done
ip link add "$bridge" type bridge stp_state 0 forward_delay 0
ip link set "$bridge" alias "$alias"
for link in "${links[@]}"; do
    read -r id local_ip remote_ip port <<< "$link"
    ip link add "svx$id" type vxlan id "$id" local "$local_ip" remote "$remote_ip" dev "$port" dstport 4789
    ip link set "svx$id" alias "$alias"
    ip link set "svx$id" master "$bridge"
    ip link set "svx$id" up
done
ip addr add "$control_cidr" dev "$bridge"
ip link set "$bridge" mtu 1450 up
ip -br addr show dev "$bridge"
