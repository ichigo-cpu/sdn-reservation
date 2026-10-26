#!/usr/bin/env python3
"""Experiment 1: throughput of h1->h3 under 5 conditions (3 runs x 10 s each).
Run with Ryu already running (Terminal A) and NO other Mininet running:
    sudo python3 tests/exp1_throughput.py
"""
import os, sys, time, json, csv, statistics, urllib.request

PROJECT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(PROJECT, 'topo'))
from topo import build                      # builds the diamond topology
from mininet.log import setLogLevel

API = 'http://127.0.0.1:8080'               # Ryu REST API
RUNS = 5
DURATION = 20
RESERVED_MBPS = 4                           # size of the reservation under test
OUT_CSV = os.path.join(PROJECT, 'results', 'exp1_throughput.csv')

# (name, reservation?, congestion?, policing?)
CONDITIONS = [
    ('baseline_idle',            False, False, False),
    ('reserved_idle',            True,  False, False),
    ('baseline_congested',       False, True,  False),
    ('reserved_congested_nopol', True,  True,  False),
    ('reserved_congested_pol',   True,  True,  True),
]


def api(method, path, body=None):
    """Tiny helper: call the Ryu REST API and return the parsed JSON reply."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def measure(h1):
    """Run iperf h1->h3 for DURATION s and return throughput in Mbps."""
    out = h1.cmd('iperf -c 10.0.0.3 -p 5001 -t %d -y C' % DURATION)
    lines = [l for l in out.strip().splitlines() if l.count(',') >= 8]
    if not lines:
        print('   !! no iperf result, raw output:', out)
        return 0.0
    mbps = float(lines[-1].split(',')[-1]) / 1e6      # bits/s -> Mbps
    if mbps < 0.5:
        print('   [raw iperf output for low result]:', out)
    return mbps


def main():
    setLogLevel('warning')
    net = build()
    net.start()
    net.staticArp()
    h1, h2, h3 = net.get('h1'), net.get('h2'), net.get('h3')
    rows = []
    try:
        print('Waiting for switches to connect to Ryu...')
        time.sleep(5)
        print('pingall:'); net.pingAll()
        # two iperf servers on h3: 5001 for h1 (measured), 5002 for h2 (flood)
        h3.cmd('iperf -s -p 5001 > /dev/null 2>&1 &')
        h3.cmd('iperf -s -p 5002 > /dev/null 2>&1 &')
        time.sleep(1)

        only = sys.argv[1:]                  # optional: run only named conditions
        for name, reserve, congest, policing in CONDITIONS:
            if only and name not in only:
                continue
            print('\n=== %s (reserve=%s congest=%s policing=%s) ===' %
                  (name, reserve, congest, policing))
            api('POST', '/policing', {'enabled': policing})
            rid = None
            if reserve:
                r = api('POST', '/reserve', {'src': '10.0.0.1', 'dst': '10.0.0.3',
                        'bw': RESERVED_MBPS, 'duration': 300, 'priority': 3,
                        'user': 'exp1'})
                print('   reserve reply:', r)
                if not r.get('ok'):
                    print('   !! reservation failed, skipping condition')
                    continue
                rid = r['res_id']
            time.sleep(2)                    # let flows/meters settle
            for run in range(1, RUNS + 1):
                if congest:                  # flood (4 TCP streams) joins 2 s AFTER h1's flow starts
                    h2.cmd('(sleep 2; iperf -c 10.0.0.3 -p 5002 -P 4 -t %d) '
                           '> /dev/null 2>&1 &' % (DURATION + 2))
                mbps = measure(h1)
                print('   run %d: %.2f Mbps' % (run, mbps))
                rows.append([name, run, round(mbps, 3)])
                h2.cmd('pkill -f "iperf -c" ')   # stop leftover flood
                time.sleep(3)
            if rid is not None:
                api('DELETE', '/reserve/%d' % rid)
            time.sleep(1)
        api('POST', '/policing', {'enabled': True})   # leave policing ON
    finally:
        h3.cmd('pkill iperf')
        net.stop()

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['condition', 'run', 'throughput_mbps'])
        w.writerows(rows)
    uid = os.environ.get('SUDO_UID')               # give file back to normal user
    if uid:
        os.chown(OUT_CSV, int(uid), int(os.environ.get('SUDO_GID', uid)))

    print('\n===== MEAN THROUGHPUT (Mbps) =====')
    for name, *_ in CONDITIONS:
        v = [r[2] for r in rows if r[0] == name]
        if v:
            sd = statistics.stdev(v) if len(v) > 1 else 0.0
            print('%-26s mean %.2f  sd %.2f  (n=%d)' % (name, statistics.mean(v), sd, len(v)))
    print('Saved:', OUT_CSV)


if __name__ == '__main__':
    main()
