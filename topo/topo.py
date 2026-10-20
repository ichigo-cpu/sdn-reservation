
#!/usr/bin/env python3
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel

def build():
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=True)
    net.addController('c0', controller=RemoteController,
                      ip='127.0.0.1', port=6633)

    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')
    s4 = net.addSwitch('s4', protocols='OpenFlow13')

    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    hs = net.addHost('hs', ip='10.0.0.4/24')

    # ORDER MATTERS: it decides port numbers
    net.addLink(h1, s1, bw=50)           # s1 p1
    net.addLink(h2, s1, bw=50)           # s1 p2
    net.addLink(s1, s2, bw=10)           # s1 p3 | s2 p1
    net.addLink(s1, s3, bw=10)           # s1 p4 | s3 p1
    net.addLink(s2, s4, bw=10)           # s2 p2 | s4 p1
    net.addLink(s3, s4, bw=10)           # s3 p2 | s4 p2
    net.addLink(s4, h3, bw=50)           # s4 p3
    net.addLink(s4, hs, bw=50)           # s4 p4
    return net

if __name__ == '__main__':
    setLogLevel('info')
    net = build()
    net.start()
    net.staticArp()
    CLI(net)
    net.stop()
#!/usr/bin/env python3
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel

def build():
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=True)
    net.addController('c0', controller=RemoteController,
                      ip='127.0.0.1', port=6633)

    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')
    s4 = net.addSwitch('s4', protocols='OpenFlow13')

    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    hs = net.addHost('hs', ip='10.0.0.4/24')

    # ORDER MATTERS: it decides port numbers
    net.addLink(h1, s1, bw=100)          # s1 p1
    net.addLink(h2, s1, bw=100)          # s1 p2
    net.addLink(s1, s2, bw=10)           # s1 p3 | s2 p1
    net.addLink(s1, s3, bw=10)           # s1 p4 | s3 p1
    net.addLink(s2, s4, bw=10)           # s2 p2 | s4 p1
    net.addLink(s3, s4, bw=10)           # s3 p2 | s4 p2
    net.addLink(s4, h3, bw=100)          # s4 p3
    net.addLink(s4, hs, bw=100)          # s4 p4
    return net

if __name__ == '__main__':
    setLogLevel('info')
    net = build()
    net.start()
    net.staticArp()   # no ARP flooding, so the loop in the diamond is harmless
    CLI(net)
    net.stop()
