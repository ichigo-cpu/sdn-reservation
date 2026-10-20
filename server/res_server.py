#!/usr/bin/env python3
"""Multithreaded TCP Reservation Server (Block 2: controller call is stubbed)."""
import socket
import threading
import itertools
import time
import json
import urllib.request

HOST = '0.0.0.0'
PORT = 9000
USERS = {'alice': 'pass1', 'bob': 'pass2', 'admin': 'adminpw'}

lock = threading.Lock()
reservations = {}          # res_id -> dict
id_counter = itertools.count(1)


def log(msg):
    print("[%s] %s" % (time.strftime('%H:%M:%S'), msg), flush=True)


CTRL = 'http://192.168.100.1:8080'
ctrl_ids = {}          # server res_id -> controller res_id


def _call(method, path, body=None):
    req = urllib.request.Request(
        CTRL + path, method=method,
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def controller_reserve(src, dst, bw, dur, prio, user='?'):
    """Ask Ryu to admit the reservation. Returns (ok, info, ctrl_id)."""
    try:
        r = _call('POST', '/reserve', {'src': src, 'dst': dst, 'bw': bw,
                                       'duration': dur, 'priority': prio,
                                       'user': user})
    except Exception as e:
        log("controller unreachable: %s" % e)
        return False, 'controller_unavailable', None
    if r.get('ok'):
        return True, r.get('path', ''), r['res_id']
    return False, r.get('reason', 'rejected'), None


def controller_release(ctrl_id):
    try:
        return _call('DELETE', '/reserve/%d' % ctrl_id).get('ok', False)
    except Exception as e:
        log("controller unreachable: %s" % e)
        return False


def handle_command(line, state):
    parts = line.strip().split()
    if not parts:
        return "ERR empty"
    cmd = parts[0].upper()

    if cmd == 'AUTH':
        if len(parts) != 3:
            return "ERR usage: AUTH <user> <token>"
        if USERS.get(parts[1]) == parts[2]:
            state['user'] = parts[1]
            return "OK authenticated"
        log("AUTH FAILED for user %s" % parts[1])
        return "REJECT bad_credentials"

    if 'user' not in state:
        return "REJECT not_authenticated"

    if cmd == 'RESERVE':
        if len(parts) != 6:
            return "ERR usage: RESERVE <src> <dst> <bw_mbps> <duration_s> <priority>"
        try:
            src, dst = parts[1], parts[2]
            bw = float(parts[3])
            dur = int(parts[4])
            prio = int(parts[5])
        except ValueError:
            return "ERR bad_number"
        if bw <= 0 or bw > 10 or dur <= 0 or not (0 <= prio <= 7):
            return "ERR out_of_range"
        with lock:
            ok, info, cid = controller_reserve(src, dst, bw, dur, prio,
                                               state['user'])
            if not ok:
                return "REJECT %s" % info
            res_id = cid
            ctrl_ids[res_id] = cid
            reservations[res_id] = {'user': state['user'], 'src': src,
                                    'dst': dst, 'bw': bw, 'dur': dur,
                                    'prio': prio}
        log("RESERVE #%d by %s: %s->%s %.1fMbps %ds prio%d" %
            (res_id, state['user'], src, dst, bw, dur, prio))
        return "OK %d" % res_id

    if cmd == 'RELEASE':
        if len(parts) != 2 or not parts[1].isdigit():
            return "ERR usage: RELEASE <res_id>"
        rid = int(parts[1])
        with lock:
            r = reservations.get(rid)
            if r is None:
                return "ERR no_such_reservation"
            if r['user'] != state['user'] and state['user'] != 'admin':
                return "REJECT not_owner"
            controller_release(ctrl_ids.get(rid, rid))
            del reservations[rid]
        log("RELEASE #%d by %s" % (rid, state['user']))
        return "OK released"

    if cmd == 'STATUS':
        with lock:
            if not reservations:
                return "OK none"
            items = ["#%d:%s:%s>%s:%sMbps" % (k, v['user'], v['src'], v['dst'], v['bw'])
                     for k, v in reservations.items()]
        return "OK " + " ".join(items)

    return "ERR unknown_command"


def client_thread(conn, addr):
    log("connected %s:%d" % addr)
    state = {}
    conn.settimeout(60)
    f = conn.makefile('rw', newline='\n')
    try:
        for line in f:
            reply = handle_command(line, state)
            f.write(reply + "\n")
            f.flush()
    except socket.timeout:
        log("timeout %s:%d" % addr)
    except (ConnectionResetError, BrokenPipeError):
        log("connection lost %s:%d" % addr)
    finally:
        try:
            f.close()
            conn.close()
        except OSError:
            pass
        log("disconnected %s:%d" % addr)


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(20)
    log("Reservation server listening on %s:%d" % (HOST, PORT))
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=client_thread, args=(conn, addr),
                             daemon=True).start()
    except KeyboardInterrupt:
        log("shutting down")
    finally:
        srv.close()


if __name__ == '__main__':
    main()
