#!/usr/bin/env python3
"""Experiment 2: reservation setup time and rejection rate vs offered load.
Run with Ryu already running (Terminal A) and NO other Mininet running:
    sudo python3 tests/exp2_load.py
"""
import os, sys, time, json, csv, statistics, subprocess, urllib.request

PROJECT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(PROJECT, 'topo'))
from topo import build, setup_mgmt, start_server
from mininet.log import setLogLevel

API = 'http://127.0.0.1:8080'
WORKER = os.path.join(PROJECT, 'tests', 'res_load_worker.py')
LEVELS = [4, 8, 10, 12, 16, 20, 24]       # total RESERVE requests per run
REPS = 3                                   # repetitions per level
BW = 2                                     # Mbps per request
DUR = 120                                  # reservation lifetime (s)
RAW_CSV = os.path.join(PROJECT, 'results', 'exp2_load_raw.csv')
SUM_CSV = os.path.join(PROJECT, 'results', 'exp2_load_summary.csv')
# (host, user, token, host IP): requests are split between these two clients
CLIENTS = [('h1', 'alice', 'pass1', '10.0.0.1'),
           ('h2', 'bob', 'pass2', '10.0.0.2')]


def api(method, path):
    req = urllib.request.Request(API + path, method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def run_level(net, n):
    """Start both clients at the same time; return their result rows."""
    counts = [n // 2 + (1 if i < n % 2 else 0) for i in range(2)]
    procs = []
    for (hname, user, tok, src), cnt in zip(CLIENTS, counts):
        if cnt == 0:
            continue
        cmd = 'python3 %s 10.0.0.4 %s %s %s 10.0.0.3 %s %d %d' % (
            WORKER, user, tok, src, BW, DUR, cnt)
        p = net.get(hname).popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, universal_newlines=True)
        procs.append((hname, p))
    rows = []
    for hname, p in procs:
        out, err = p.communicate(timeout=120)
        if err.strip():
            print('   worker error on %s: %s' % (hname, err.strip()))
        for line in out.splitlines():
            parts = line.split(',', 3)
            if len(parts) == 4:
                rows.append([hname, parts[0], parts[1], float(parts[2]), parts[3]])
    return rows


def pct(vals, p):
    v = sorted(vals)
    return v[int(p * (len(v) - 1))] if v else 0.0


def main():
    setLogLevel('warning')
    net = build()
    net.start()
    net.staticArp()
    raw = []
    try:
        print('Waiting for switches to connect to Ryu...')
        time.sleep(5)
        print('pingall:'); net.pingAll()
        setup_mgmt(net)
        start_server(net)
        time.sleep(3)

        for n in LEVELS:
            for rep in range(1, REPS + 1):
                rows = run_level(net, n)
                res = [r for r in rows if r[2] == 'RESERVE']
                ok = sum(1 for r in res if r[4].startswith('OK'))
                rej = sum(1 for r in res if r[4].startswith('REJECT'))
                print('load=%2d rep=%d: sent=%d admitted=%d rejected=%d' %
                      (n, rep, len(res), ok, rej))
                for r in rows:
                    raw.append([n, rep] + r)
                left = api('GET', '/reservations')       # safety cleanup
                for k in left:
                    api('DELETE', '/reserve/%s' % k)
                if left:
                    print('   cleaned %d leftover reservations' % len(left))
                time.sleep(1)
    finally:
        net.stop()

    os.makedirs(os.path.dirname(RAW_CSV), exist_ok=True)
    with open(RAW_CSV, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['load', 'rep', 'client', 'idx', 'cmd', 'latency_ms', 'reply'])
        w.writerows(raw)

    summary = []
    print('\n===== SUMMARY (RESERVE requests, %d Mbps each, %d reps per load) =====' % (BW, REPS))
    print('%5s %6s %9s %9s %8s %9s %9s %9s' %
          ('load', 'sent', 'admitted', 'rejected', 'rej_%', 'mean_ms', 'med_ms', 'p95_ms'))
    for n in LEVELS:
        res = [r for r in raw if r[0] == n and r[4] == 'RESERVE']
        lat = [r[5] for r in res]
        ok = sum(1 for r in res if r[6].startswith('OK'))
        rej = sum(1 for r in res if r[6].startswith('REJECT'))
        sent = len(res)
        row = [n, sent, ok, rej, round(100.0 * rej / sent, 1) if sent else 0.0,
               round(statistics.mean(lat), 2) if lat else 0.0,
               round(statistics.median(lat), 2) if lat else 0.0,
               round(pct(lat, 0.95), 2)]
        summary.append(row)
        print('%5d %6d %9d %9d %8.1f %9.2f %9.2f %9.2f' % tuple(row))
    with open(SUM_CSV, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['load', 'sent', 'admitted', 'rejected', 'reject_pct',
                    'mean_ms', 'median_ms', 'p95_ms'])
        w.writerows(summary)
    conn = [r[5] for r in raw if r[4] == 'CONNECT']
    auth = [r[5] for r in raw if r[4] == 'AUTH']
    rel = [r[5] for r in raw if r[4] == 'RELEASE']
    print('\nTCP connect mean %.2f ms | AUTH mean %.2f ms | RELEASE mean %.2f ms' %
          (statistics.mean(conn), statistics.mean(auth),
           statistics.mean(rel) if rel else 0.0))
    uid = os.environ.get('SUDO_UID')
    if uid:
        for p in (RAW_CSV, SUM_CSV):
            os.chown(p, int(uid), int(os.environ.get('SUDO_GID', uid)))
    print('Saved:', RAW_CSV, 'and', SUM_CSV)


if __name__ == '__main__':
    main()
