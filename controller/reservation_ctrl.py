import csv
import json
import os
import time
import threading
from collections import defaultdict

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, MAIN_DISPATCHER,
                                    set_ev_cls)
from ryu.ofproto import ofproto_v1_3
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.lib import hub
from webob import Response

# ---------------- static topology knowledge ----------------
DEFAULT_ROUTES = {
    1: {'10.0.0.1': 1, '10.0.0.2': 2, '10.0.0.3': 3, '10.0.0.4': 3},
    2: {'10.0.0.1': 1, '10.0.0.2': 1, '10.0.0.3': 2, '10.0.0.4': 2},
    3: {'10.0.0.1': 1, '10.0.0.2': 1, '10.0.0.3': 2, '10.0.0.4': 2},
    4: {'10.0.0.1': 1, '10.0.0.2': 1, '10.0.0.3': 3, '10.0.0.4': 4},
}
# Entries that change when the default traffic must use the backup path
BACKUP_OVERRIDE = {
    1: {'10.0.0.3': 4, '10.0.0.4': 4},
    4: {'10.0.0.1': 2, '10.0.0.2': 2},
}
# (from_switch, to_switch) -> output port on from_switch
PORTS = {(1, 2): 3, (1, 3): 4, (2, 1): 1, (2, 4): 2,
         (3, 1): 1, (3, 4): 2, (4, 2): 1, (4, 3): 2}
# host ip -> (switch, port)
HOSTS = {'10.0.0.1': (1, 1), '10.0.0.2': (1, 2),
         '10.0.0.3': (4, 3), '10.0.0.4': (4, 4)}
PATHS = [('primary', [1, 2, 4]), ('backup', [1, 3, 4])]
LINK_CAP = 10.0          # Mbps per inter-switch link
RES_PRIORITY = 100       # flow priority for reserved traffic (default flows = 10)
POLL_S = 2               # port-stats polling interval (seconds)
RESULTS_DIR = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'results'))


class ReservationCtrl(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(ReservationCtrl, self).__init__(*args, **kwargs)
        self.dps = {}
        self.res = {}                       # res_id -> dict
        self.reserved = defaultdict(float)  # (a, b) -> Mbps reserved
        self.next_id = 1
        self.lock = threading.Lock()
        self.down_links = set()             # set of frozenset({a, b})
        self.default_path = 'primary'       # which path default flows use
        self.last_stats = {}                # (dpid, port) -> (time, tx_bytes)

        os.makedirs(RESULTS_DIR, exist_ok=True)
        self.util_f = open(os.path.join(RESULTS_DIR, 'utilization.csv'),
                           'w', newline='')
        self.util_w = csv.writer(self.util_f)
        self.util_w.writerow(['time', 'link', 'tx_mbps', 'reserved_mbps'])
        self.util_f.flush()
        self.ev_f = open(os.path.join(RESULTS_DIR, 'events.csv'),
                         'w', newline='')
        self.ev_w = csv.writer(self.ev_f)
        self.ev_w.writerow(['time', 'event', 'detail'])
        self.ev_f.flush()

        kwargs['wsgi'].register(ResAPI, {'app': self})
        hub.spawn(self._expiry_loop)
        hub.spawn(self._monitor_loop)

    # ---------- event log ----------
    def _event(self, name, detail=''):
        self.logger.info("%s %s", name, detail)
        self.ev_w.writerow([round(time.time(), 3), name, detail])
        self.ev_f.flush()

    # ---------- default flows ----------
    def _routes_for(self, dpid):
        routes = dict(DEFAULT_ROUTES.get(dpid, {}))
        if self.default_path == 'backup':
            routes.update(BACKUP_OVERRIDE.get(dpid, {}))
        return routes

    def _push_defaults(self, dp):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        routes = self._routes_for(dp.id)
        for ip, port in routes.items():
            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip)
            inst = [parser.OFPInstructionActions(
                ofp.OFPIT_APPLY_ACTIONS, [parser.OFPActionOutput(port)])]
            dp.send_msg(parser.OFPFlowMod(
                datapath=dp, priority=10, match=match, instructions=inst))
        return len(routes)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_switch(self, ev):
        dp = ev.msg.datapath
        self.dps[dp.id] = dp
        n = self._push_defaults(dp)
        self.logger.info("Installed %d default flows on s%d", n, dp.id)

    def _refresh_defaults(self):          # caller holds the lock
        primary_ok = self._path_up(PATHS[0][1])
        backup_ok = self._path_up(PATHS[1][1])
        new = 'backup' if (not primary_ok and backup_ok) else 'primary'
        if new != self.default_path:
            self.default_path = new
            for dp in self.dps.values():
                self._push_defaults(dp)
            self._event('DEFAULT_ROUTES', new)

    # ---------- path helpers ----------
    @staticmethod
    def _links(path):
        return [(path[i], path[i + 1]) for i in range(len(path) - 1)]

    def _path_up(self, path):
        return all(frozenset(l) not in self.down_links
                   for l in self._links(path))

    def _fits(self, path, bw):
        return all(self.reserved[l] + bw <= LINK_CAP + 1e-9
                   for l in self._links(path))

    def _choose_path(self, bw):
        for name, path in PATHS:
            if not all(sw in self.dps for sw in path):
                continue
            if not self._path_up(path):
                continue
            if self._fits(path, bw):
                return name, path
        return None

    # ---------- admission control ----------
    def reserve(self, src, dst, bw, dur, prio, user='?'):
        with self.lock:
            if src not in HOSTS or dst not in HOSTS:
                return False, 'unknown_host', None
            if HOSTS[src][0] != 1 or HOSTS[dst][0] != 4:
                return False, 'unsupported_endpoints', None
            chosen = self._choose_path(bw)
            if chosen is None:
                self._event('REJECT', "%s->%s %.1fMbps (no capacity)"
                            % (src, dst, bw))
                return False, 'insufficient_bandwidth', None
            name, path = chosen
            rid = self.next_id
            self.next_id += 1
            self._install(rid, path, src, dst, bw)
            for l in self._links(path):
                self.reserved[l] += bw
            self.res[rid] = {'src': src, 'dst': dst, 'bw': bw, 'prio': prio,
                             'user': user, 'path': name, 'hops': path,
                             'expires': time.time() + dur}
            self._event('ADMIT', "#%d %s->%s %.1fMbps via %s %s"
                        % (rid, src, dst, bw, name, path))
            return True, name, rid

    def release(self, rid):
        with self.lock:
            return self._remove(rid)

    def _uninstall(self, rid, hops):      # delete flows + meters on a path
        for sw in hops:
            dp = self.dps.get(sw)
            if dp is None:
                continue
            ofp, p = dp.ofproto, dp.ofproto_parser
            dp.send_msg(p.OFPFlowMod(
                datapath=dp, command=ofp.OFPFC_DELETE, cookie=rid,
                cookie_mask=0xffffffffffffffff, out_port=ofp.OFPP_ANY,
                out_group=ofp.OFPG_ANY, match=p.OFPMatch()))
            dp.send_msg(p.OFPMeterMod(
                datapath=dp, command=ofp.OFPMC_DELETE,
                flags=ofp.OFPMF_KBPS, meter_id=rid, bands=[]))

    def _remove(self, rid):       # caller holds the lock
        r = self.res.pop(rid, None)
        if r is None:
            return False
        self._uninstall(rid, r['hops'])
        for l in self._links(r['hops']):
            self.reserved[l] = max(0.0, self.reserved[l] - r['bw'])
        self._event('REMOVED', '#%d' % rid)
        return True

    def _install(self, rid, path, src, dst, bw):
        kbps = int(bw * 1000)
        for i, sw in enumerate(path):
            dp = self.dps[sw]
            ofp, p = dp.ofproto, dp.ofproto_parser
            if i == len(path) - 1:
                out = HOSTS[dst][1]
            else:
                out = PORTS[(sw, path[i + 1])]
            band = p.OFPMeterBandDrop(rate=kbps, burst_size=max(kbps // 10, 50))
            dp.send_msg(p.OFPMeterMod(
                datapath=dp, command=ofp.OFPMC_ADD, flags=ofp.OFPMF_KBPS,
                meter_id=rid, bands=[band]))
            match = p.OFPMatch(eth_type=0x0800, ipv4_src=src, ipv4_dst=dst)
            inst = [p.OFPInstructionMeter(rid),
                    p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                            [p.OFPActionOutput(out)])]
            dp.send_msg(p.OFPFlowMod(
                datapath=dp, cookie=rid, priority=RES_PRIORITY,
                match=match, instructions=inst))

    # ---------- auto-expiry ----------
    def _expiry_loop(self):
        while True:
            hub.sleep(1)
            now = time.time()
            with self.lock:
                for rid in [r for r, v in self.res.items() if v['expires'] <= now]:
                    self._event('EXPIRED', '#%d' % rid)
                    self._remove(rid)

    # ---------- BLOCK 4a: link-failure detection + rerouting ----------
    @staticmethod
    def _link_of(dpid, port):
        for (a, b), p in PORTS.items():
            if a == dpid and p == port:
                return frozenset((a, b))
        return None

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def on_port_status(self, ev):
        t0 = time.time()
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        link = self._link_of(dp.id, msg.desc.port_no)
        if link is None:
            return                          # host-facing port, ignore
        is_down = bool(msg.desc.state & ofp.OFPPS_LINK_DOWN) or \
            bool(msg.desc.config & ofp.OFPPC_PORT_DOWN)
        with self.lock:
            if is_down == (link in self.down_links):
                return                      # nothing changed
            a, b = sorted(link)
            if is_down:
                self.down_links.add(link)
                self._event('LINK_DOWN', 's%d-s%d' % (a, b))
                self._refresh_defaults()
                self._reroute_affected()
                self._event('FAILOVER_DONE', '%.1f ms'
                            % ((time.time() - t0) * 1000))
            else:
                self.down_links.discard(link)
                self._event('LINK_UP', 's%d-s%d' % (a, b))
                self._refresh_defaults()

    def _reroute_affected(self):           # caller holds the lock
        for rid in list(self.res):
            r = self.res[rid]
            if self._path_up(r['hops']):
                continue
            t1 = time.time()
            self._uninstall(rid, r['hops'])
            for l in self._links(r['hops']):
                self.reserved[l] = max(0.0, self.reserved[l] - r['bw'])
            chosen = self._choose_path(r['bw'])
            if chosen is None:
                del self.res[rid]
                self._event('DROPPED', '#%d no alternative path' % rid)
                continue
            name, path = chosen
            self._install(rid, path, r['src'], r['dst'], r['bw'])
            for l in self._links(path):
                self.reserved[l] += r['bw']
            r['path'] = name
            r['hops'] = path
            self._event('REROUTED', '#%d -> %s %s in %.1f ms'
                        % (rid, name, path, (time.time() - t1) * 1000))

    # ---------- BLOCK 4b: port-stats monitoring ----------
    def _monitor_loop(self):
        while True:
            hub.sleep(POLL_S)
            for dp in list(self.dps.values()):
                parser = dp.ofproto_parser
                dp.send_msg(parser.OFPPortStatsRequest(
                    dp, 0, dp.ofproto.OFPP_ANY))

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def on_port_stats(self, ev):
        dp = ev.msg.datapath
        now = time.time()
        for st in ev.msg.body:
            key = (dp.id, st.port_no)
            prev = self.last_stats.get(key)
            self.last_stats[key] = (now, st.tx_bytes)
            if prev is None:
                continue
            link = None
            for k, p in PORTS.items():
                if k[0] == dp.id and p == st.port_no:
                    link = k
                    break
            if link is None:
                continue                    # host-facing port
            dt = now - prev[0]
            if dt <= 0:
                continue
            mbps = (st.tx_bytes - prev[1]) * 8.0 / dt / 1e6
            self.util_w.writerow([round(now, 3), 's%d->s%d' % link,
                                  round(mbps, 3),
                                  self.reserved.get(link, 0.0)])
        self.util_f.flush()


# ---------------- REST API ----------------
def _json(obj, status=200):
    return Response(content_type='application/json', status=status,
                    body=json.dumps(obj).encode('utf-8'))


class ResAPI(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(ResAPI, self).__init__(req, link, data, **config)
        self.app = data['app']

    @route('res', '/reserve', methods=['POST'])
    def post_reserve(self, req, **kw):
        try:
            b = json.loads(req.body.decode('utf-8'))
            src, dst = str(b['src']), str(b['dst'])
            bw, dur, prio = float(b['bw']), int(b['duration']), int(b['priority'])
            user = str(b.get('user', '?'))
        except Exception:
            return _json({'ok': False, 'reason': 'bad_request'}, 400)
        ok, info, rid = self.app.reserve(src, dst, bw, dur, prio, user)
        if ok:
            return _json({'ok': True, 'res_id': rid, 'path': info})
        return _json({'ok': False, 'reason': info})

    @route('res', '/reserve/{rid}', methods=['DELETE'])
    def delete_reserve(self, req, rid, **kw):
        try:
            rid = int(rid)
        except ValueError:
            return _json({'ok': False, 'reason': 'bad_id'}, 400)
        return _json({'ok': self.app.release(rid)})

    @route('res', '/reservations', methods=['GET'])
    def get_reservations(self, req, **kw):
        now = time.time()
        out = {str(k): {'src': v['src'], 'dst': v['dst'], 'bw': v['bw'],
                        'prio': v['prio'], 'user': v['user'], 'path': v['path'],
                        'remaining_s': int(v['expires'] - now)}
               for k, v in list(self.app.res.items())}
        return _json(out)

    @route('res', '/links', methods=['GET'])
    def get_links(self, req, **kw):
        out = {"s%d-s%d" % k: {'reserved_mbps': v, 'capacity_mbps': LINK_CAP}
               for k, v in list(self.app.reserved.items())}
        return _json(out)

    @route('res', '/status', methods=['GET'])
    def get_status(self, req, **kw):
        down = ["s%d-s%d" % tuple(sorted(l)) for l in self.app.down_links]
        return _json({'down_links': down,
                      'default_path': self.app.default_path})
