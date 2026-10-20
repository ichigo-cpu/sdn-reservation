from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3

# dpid -> { dst_ip : out_port }   (primary path s1-s2-s4)
DEFAULT_ROUTES = {
    1: {'10.0.0.1': 1, '10.0.0.2': 2, '10.0.0.3': 3, '10.0.0.4': 3},
    2: {'10.0.0.1': 1, '10.0.0.2': 1, '10.0.0.3': 2, '10.0.0.4': 2},
    3: {'10.0.0.1': 1, '10.0.0.2': 1, '10.0.0.3': 2, '10.0.0.4': 2},
    4: {'10.0.0.1': 1, '10.0.0.2': 1, '10.0.0.3': 3, '10.0.0.4': 4},
}

class BaseCtrl(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_switch(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        routes = DEFAULT_ROUTES.get(dp.id, {})
        for ip, port in routes.items():
            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip)
            actions = [parser.OFPActionOutput(port)]
            inst = [parser.OFPInstructionActions(
                ofp.OFPIT_APPLY_ACTIONS, actions)]
            dp.send_msg(parser.OFPFlowMod(
                datapath=dp, priority=10, match=match, instructions=inst))
        self.logger.info("Installed %d default flows on s%d",
                         len(routes), dp.id)
