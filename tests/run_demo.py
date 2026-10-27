#!/usr/bin/env python3
"""One-command demo. Start Ryu first (Terminal A), no other Mininet open.
    sudo python3 tests/run_demo.py
"""
import os, sys, time, json, urllib.request

PROJECT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(PROJECT, 'topo'))
from topo import build, setup_mgmt, start_server
from mininet.log import setLogLevel

API = 'http://127.0.0.1:8080'
CLIENT = PROJECT + '/client/res_client.py'


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def banner(text):
    print('\n' + '=' * 60 + '\n' + text + '\n' + '=' * 60)


def iperf(h1, secs):
    out = h1.cmd('iperf -c 10.0.0.3 -p 5001 -t %d -y C' % secs)
    lines = [l for l in out.strip().splitlines() if l.count(',') >= 8]
    return float(lines[-1].split(',')[-1]) / 1e6 if lines else 0.0


def reserve(host, user, token, cmds):
    print(host.cmd('python3 %s 10.0.0.4 %s %s "%s"' % (CLIENT, user, token, cmds)))


def release_all():
    for rid in api('GET', '/reservations'):
        api('DELETE', '/reserve/%s' % rid)


def main():
    setLogLevel('warning')
    net = build()
    net.start()
    net.staticArp()
    setup_mgmt(net)
    start_server(net)
    h1, h2, h3 = net.get('h1'), net.get('h2'), net.get('h3')
    try:
        print('Waiting for switches to connect to Ryu...')
        time.sleep(5)
        h3.cmd('iperf -s -p 5001 > /dev/null 2>&1 &')
        h3.cmd('iperf -s -p 5002 > /dev/null 2>&1 &')
        time.sleep(1)
        api('POST', '/policing', {'enabled': False})

        banner('STAGE 1: connectivity (pingall)')
        net.pingAll()

        banner('STAGE 2: reserve 4 Mbps h1 -> h3 over TCP')
        print('Without reservation: %.2f Mbps' % iperf(h1, 8))
        reserve(h1, 'alice', 'pass1', 'RESERVE 10.0.0.1 10.0.0.3 4 300 3')
        print('With reservation:    %.2f Mbps' % iperf(h1, 8))

        banner('STAGE 3: admission control (4+4 primary, 4 backup, 7 rejected)')
        reserve(h2, 'bob', 'pass2', 'RESERVE 10.0.0.2 10.0.0.3 4 300 3; '
                'RESERVE 10.0.0.2 10.0.0.3 4 300 3; RESERVE 10.0.0.2 10.0.0.3 7 300 3')
        print('/links:', api('GET', '/links'))
        release_all()
        time.sleep(1)

        banner('STAGE 4: congestion, reserved 4 Mbps flow vs h2 flood (4 TCP streams)')
        reserve(h1, 'alice', 'pass1', 'RESERVE 10.0.0.1 10.0.0.3 4 300 3')
        for pol in (False, True):
            api('POST', '/policing', {'enabled': pol})
            time.sleep(2)
            h2.cmd('(sleep 2; iperf -c 10.0.0.3 -p 5002 -P 4 -t 14) > /dev/null 2>&1 &')
            print('policing %-3s: %.2f Mbps' % ('ON' if pol else 'OFF', iperf(h1, 12)))
            h2.cmd('pkill -f "iperf -c"')
            time.sleep(3)
        release_all()
        time.sleep(1)

        banner('STAGE 5: link failure s1-s2 at +4 s, restored at +8 s')
        reserve(h1, 'alice', 'pass1', 'RESERVE 10.0.0.1 10.0.0.3 4 300 3')
        h1.cmd('iperf -c 10.0.0.3 -p 5001 -t 12 -i 1 -y C > /tmp/demo_fail.txt 2>&1 &')
        time.sleep(4)
        net.configLinkStatus('s1', 's2', 'down')
        time.sleep(4)
        net.configLinkStatus('s1', 's2', 'up')
        time.sleep(6)
        for line in open('/tmp/demo_fail.txt'):
            f = line.strip().split(',')
            if len(f) >= 9:
                print('  %s s -> %.2f Mbps' % (f[6], float(f[-1]) / 1e6))
        print('Controller events (results/events.csv):')
        os.system('tail -n 8 %s/results/events.csv' % PROJECT)
        release_all()
    finally:
        h3.cmd('pkill iperf')
        net.stop()


if __name__ == '__main__':
    main()
