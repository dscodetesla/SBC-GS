#!/usr/bin/env python3
"""FC stand-in for the bridge fault tests (ONE process serves several scenarios to save pymavlink start-up time).
Listens on UDP 127.0.0.1:<port> for every "port:outfile" argument, answers with HEARTBEATs (sysid 1) once a peer is known and logs every
RC_CHANNELS_OVERRIDE as "<wall time> <target> ch1..ch8" (flushed per frame); writes READY first and END last. Stops when <stopfile> exists
or after 60 s. Run with a python that has pymavlink.   Usage: fc_sniff.py <stopfile> <port>:<outfile> [<port>:<outfile> ...]"""
import os
import sys
import time

from pymavlink import mavutil

stop = sys.argv[1]
chans = []
for spec in sys.argv[2:]:
    port, out = spec.split(":", 1)
    f = open(out, "w", buffering=1)
    f.write("READY\n")
    m = mavutil.mavlink_connection(f"udpin:127.0.0.1:{int(port)}", source_system=1, source_component=1)
    chans.append({"m": m, "f": f, "peer": False, "last": 0.0})
t0 = time.time()
while time.time() - t0 < 60 and not os.path.exists(stop):
    busy = False
    for c in chans:
        while True:
            msg = c["m"].recv_match(blocking=False)
            if msg is None:
                break
            busy = True
            now = time.time()
            c["peer"] = True
            if msg.get_type() == "RC_CHANNELS_OVERRIDE":
                c["f"].write("%.4f %d %d %d %d %d %d %d %d %d\n" % (now, msg.target_system, msg.chan1_raw, msg.chan2_raw, msg.chan3_raw, msg.chan4_raw,
                                                                    msg.chan5_raw, msg.chan6_raw, msg.chan7_raw, msg.chan8_raw))
        now = time.time()
        if c["peer"] and now - c["last"] >= 0.5:
            c["last"] = now
            c["m"].mav.heartbeat_send(2, 3, 0, 0, 4)
    if not busy:
        time.sleep(0.002)
for c in chans:
    c["f"].write("END %.4f\n" % time.time())
    c["f"].close()
