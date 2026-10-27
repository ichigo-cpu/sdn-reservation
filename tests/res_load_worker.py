#!/usr/bin/env python3
"""Load worker: runs ON a Mininet host. One TCP connection to the reservation
server: AUTH, then <count> RESERVE requests, then RELEASE of every admitted one.
Prints CSV lines: idx,cmd,latency_ms,reply
usage: res_load_worker.py <server_ip> <user> <token> <src> <dst> <bw> <dur> <count>
"""
import socket, sys, time

server, user, token, src, dst, bw, dur, count = sys.argv[1:9]
count = int(count)
PORT = 9000
rows = []

t = time.perf_counter()
s = socket.create_connection((server, PORT), timeout=30)       # TCP handshake
rows.append((0, 'CONNECT', (time.perf_counter() - t) * 1000, 'connected'))
f = s.makefile('rw', newline='\n')


def rpc(line):
    """Send one command line, wait for the one-line reply, return (ms, reply)."""
    t0 = time.perf_counter()
    f.write(line + '\n')
    f.flush()
    reply = f.readline().strip() or 'ERR no_reply'
    return (time.perf_counter() - t0) * 1000, reply


ms, r = rpc('AUTH %s %s' % (user, token))
rows.append((0, 'AUTH', ms, r))

ids = []
for i in range(1, count + 1):
    ms, r = rpc('RESERVE %s %s %s %s 3' % (src, dst, bw, dur))
    rows.append((i, 'RESERVE', ms, r))
    if r.startswith('OK '):
        ids.append(r.split()[1])

for i, rid in enumerate(ids, 1):                                # clean up
    ms, r = rpc('RELEASE %s' % rid)
    rows.append((i, 'RELEASE', ms, r))

s.close()
for idx, cmd, ms, r in rows:
    print('%d,%s,%.3f,%s' % (idx, cmd, ms, r))
