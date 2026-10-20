#!/usr/bin/env python3
"""CLI client. Usage:
  res_client.py <server_ip> <user> <token> "<commands separated by ;>"
  e.g. res_client.py 10.0.0.4 alice pass1 "RESERVE 10.0.0.1 10.0.0.3 4 60 5; STATUS"
"""
import socket
import sys
import time

PORT = 9000


def main():
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(1)
    server, user, token = sys.argv[1], sys.argv[2], sys.argv[3]
    commands = " ".join(sys.argv[4:]).split(';')

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect((server, PORT))
    except OSError as e:
        print("ERR cannot connect: %s" % e)
        sys.exit(2)
    f = s.makefile('rw', newline='\n')

    def send(cmd):
        t0 = time.time()
        f.write(cmd.strip() + "\n")
        f.flush()
        reply = f.readline().strip()
        ms = (time.time() - t0) * 1000
        print("%s  ->  %s   (%.1f ms)" % (cmd.strip(), reply, ms))
        return reply

    send("AUTH %s %s" % (user, token))
    for c in commands:
        if c.strip():
            send(c)
    s.close()


if __name__ == '__main__':
    main()
