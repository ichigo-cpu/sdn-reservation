#!/bin/bash
# Creates the management link between the host machine (Ryu) and hs.
# Run with sudo AFTER Mininet is up.
PID=$(pgrep -f "mininet:hs" | head -n1)
if [ -z "$PID" ]; then echo "hs not found - is Mininet running?"; exit 1; fi
ip link del veth-hs 2>/dev/null
ip link add veth-hs type veth peer name veth-ctl
ip link set veth-ctl netns "$PID"
ip addr add 192.168.100.1/24 dev veth-hs
ip link set veth-hs up
nsenter -t "$PID" -n ip addr add 192.168.100.2/24 dev veth-ctl
nsenter -t "$PID" -n ip link set veth-ctl up
echo "Management link ready (hs PID $PID)"
