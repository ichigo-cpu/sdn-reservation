#!/usr/bin/env python3
"""Experiment 3: link failure (s1-s2 down at +4 s), with and without a reservation.
Run with Ryu already running and NO other Mininet running:
    sudo python3 tests/exp3_failover.py
"""
import os, sys, time, re, csv, json, shutil, subprocess, statistics, urllib.request

PROJECT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(PROJECT, 'topo'))
from topo import build, setup_mgmt, start_server
from mininet.log import setLogLevel

API = 'http://127.0.0.1:8080'
OUT = os.path.join(PROJECT, 'results', 'exp3_failover.csv')
EV = os.path.join(PROJECT, 'results', 'events.csv')
EV_COPY = os.path.join(PROJECT, 'results', 'exp3_events.csv')
REPS, T, FAIL_AT = 3, 12, 4
RES_CMD = ("python3 -c \"import socket;s=socket.create_connection(('10.0.0.4',9000),timeout=10);"
           "f=s.makefile('rw');f.write('AUTH alice pass1\\n');f.flush();f.readline();"
           "f.write('RESERVE 10.0.0.1 10.0.0.3 4 120 3\\n');f.flush();print(f.readline().strip())\"")
PAT = re.compile(r'(\d+\.\d+)\s*-\s*(\d+\.\d+)\s+sec\s+[\d.]+\s+\S+\s+([\d.]+)\s+Mbits/sec')


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def run_once(net, reserve):
    h1 = net.get('h1')
    rid = None
    if reserve:
        out = h1.cmd(RES_CMD).strip()
        print('   reserve reply:', out)
        if not out.startswith('OK '):
            return []
        rid = out.split()[1]
    p = h1.popen('iperf -c 10.0.0.3 -t %d -i 1 -f m' % T, stdout=subprocess.PIPE,
                 stderr=subprocess.STDOUT, universal_newlines=True)
    time.sleep(FAIL_AT)
    net.configLinkStatus('s1', 's2', 'down')
    print('   s1-s2 DOWN at +%d s' % FAIL_AT)
    out, _ = p.communicate(timeout=T + 30)
    net.configLinkStatus('s1', 's2', 'up')
    if rid:
        api('DELETE', '/reserve/%s' % rid)
    time.sleep(3)
    secs = []
    for m in PAT.finditer(out):
        a, b = float(m.group(1)), float(m.group(2))
        if b - a < 1.5:
            secs.append((int(round(b)), float(m.group(3))))
    return secs


def main():
    setLogLevel('warning')
    net = build()
    net.start()
    net.staticArp()
    rows = []
    try:
        print('Waiting for switches to connect to Ryu...')
        time.sleep(5)
        print('pingall:'); net.pingAll()
        setup_mgmt(net)
        start_server(net)
        time.sleep(3)
        api('POST', '/policing', {'enabled': False})
        net.get('h3').cmd('iperf -s > /dev/null 2>&1 &')
        time.sleep(1)
        for cond, reserve in (('no_reservation', False), ('with_reservation', True)):
            for rep in range(1, REPS + 1):
                print('%s rep %d' % (cond, rep))
                secs = run_once(net, reserve)
                print('   per-second Mbps:', [v for _, v in secs])
                for s, v in secs:
                    rows.append([cond, rep, s, v])
    finally:
        net.get('h3').cmd('pkill -f "iperf -s"')
        net.stop()

    with open(OUT, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['condition', 'rep', 'second', 'mbps'])
        w.writerows(rows)
    print('\n===== SUMMARY (link down at +%d s) =====' % FAIL_AT)
    print('%-17s %8s %10s %9s %10s' % ('condition', 'before', 'min_after', 'zero_s', 'recovered'))
    for cond in ('no_reservation', 'with_reservation'):
        b, m, z, a = [], [], [], []
        for rep in range(1, REPS + 1):
            r = [(s, v) for c, rp, s, v in rows if c == cond and rp == rep]
            b += [v for s, v in r if 2 <= s <= FAIL_AT]
            w3 = [v for s, v in r if FAIL_AT < s <= FAIL_AT + 3]
            m.append(min(w3) if w3 else 0.0)
            z.append(sum(1 for s, v in r if s > FAIL_AT and v < 0.1))
            a += [v for s, v in r if s > FAIL_AT + 3]
        print('%-17s %8.2f %10.2f %9.1f %10.2f' % (cond,
              statistics.mean(b) if b else 0, statistics.mean(m),
              statistics.mean(z), statistics.mean(a) if a else 0))
    print('\nController events this session (raw rows):')
    if os.path.exists(EV):
        shutil.copy(EV, EV_COPY)
        for line in open(EV):
            if any(k in line for k in ('LINK_DOWN', 'REROUTED', 'FAILOVER_DONE', 'LINK_UP')):
                print('  ', line.strip())
    uid = os.environ.get('SUDO_UID')
    if uid:
        for p in (OUT, EV_COPY):
            if os.path.exists(p):
                os.chown(p, int(uid), int(os.environ.get('SUDO_GID', uid)))
    print('Saved:', OUT, 'and', EV_COPY)


if __name__ == '__main__':
    main()
