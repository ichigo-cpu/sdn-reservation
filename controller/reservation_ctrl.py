import json
import time
import threading
from collections import defaultdict

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, set_ev_cls
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
# (from_switch, to_switch) -> output port on from_switch
PORTS = {(1, 2): 3, (1, 3): 4, (2, 1): 1, (2, 4): 2,
         (3, 1): 1, (3, 4): 2, (4, 2): 1, (4, 3): 2}
# host ip -> (switch, port)
HOSTS = {'10.0.0.1': (1, 1), '10.0.0.2': (1, 2),
         '10.0.0.3': (4, 3), '10.0.0.4': (4, 4)}
PATHS = [('primary', [1, 2, 4]), ('backup', [1, 3, 4])]
LINK_CAP = 10.0          # Mbps per inter-switch link
RES_PRIORITY = 100       # flow priority for reserved traffic (default flows = 10)


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
        kwargs['wsgi'].register(ResAPI, {'app': self})
        hub.spawn(self._expiry_loop)

    # ---------- default flows ----------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_switch(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        self.dps[dp.id] = dp
        routes = DEFAULT_ROUTES.get(dp.id, {})
        for ip, port in routes.items():
            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip)
            inst = [parser.OFPInstructionActions(
                ofp.OFPIT_APPLY_ACTIONS, [parser.OFPActionOutput(port)])]
            dp.send_msg(parser.OFPFlowMod(
                datapath=dp, priority=10, match=match, instructions=inst))
        self.logger.info("Installed %d default flows on s%d", len(routes), dp.id)

    # ---------- admission control ----------
    @staticmethod
    def _links(path):
        return [(path[i], path[i + 1]) for i in range(len(path) - 1)]

    def _fits(self, path, bw):
        return all(self.reserved[l] + bw <= LINK_CAP + 1e-9
                   for l in self._links(path))

    def reserve(self, src, dst, bw, dur, prio, user='?'):
        with self.lock:
            if src not in HOSTS or dst not in HOSTS:
                return False, 'unknown_host', None
            if HOSTS[src][0] != 1 or HOSTS[dst][0] != 4:
                return False, 'unsupported_endpoints', None
            chosen = None
            for name, path in PATHS:
                if not all(sw in self.dps for sw in path):
                    continue
                if self._fits(path, bw):
                    chosen = (name, path)
                    break
            if chosen is None:
                self.logger.info("REJECT %s->%s %.1fMbps (no capacity)", src, dst, bw)
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
            self.logger.info("ADMIT #%d %s->%s %.1fMbps via %s (%s)",
                             rid, src, dst, bw, name, path)
            return True, name, rid

    def release(self, rid):
        with self.lock:
            return self._remove(rid)

    def _remove(self, rid):       # caller holds the lock
        r = self.res.pop(rid, None)
        if r is None:
            return False
        for sw in r['hops']:
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
        for l in self._links(r['hops']):
            self.reserved[l] = max(0.0, self.reserved[l] - r['bw'])
        self.logger.info("REMOVED #%d", rid)
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
                    self.logger.info("EXPIRED #%d", rid)
                    self._remove(rid)


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
               for k, v in self.app.res.items()}
        return _json(out)

    @route('res', '/links', methods=['GET'])
    def get_links(self, req, **kw):
        out = {"s%d-s%d" % k: {'reserved_mbps': v, 'capacity_mbps': LINK_CAP}
               for k, v in self.app.reserved.items()}
        return _json(out)

