import io
import json
import subprocess
import sys
import time
from functools import partial

import matplotlib
from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.topo import SingleSwitchTopo
from os_ken.ofproto import ofproto_v1_3, ofproto_v1_3_parser


def run(*args):
    print("+", " ".join(args), flush=True)
    result = subprocess.run(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=30,
    )
    print(result.stdout, end="", flush=True)
    result.check_returncode()
    return result.stdout.strip()


def main():
    expected_arch = sys.argv[1]
    actual_arch = run("dpkg", "--print-architecture")
    if actual_arch != expected_arch:
        raise RuntimeError(f"Expected {expected_arch}, got {actual_arch}")

    for command in (
        ("ovs-vsctl", "--version"),
        ("ovs-vswitchd", "--version"),
        ("/usr/lib/frr/bgpd", "--version"),
        ("iperf3", "--version"),
        ("tc", "-V"),
        ("tcpdump", "--version"),
    ):
        run(*command)
    run("ovs-vsctl", "--timeout=10", "show")
    if ofproto_v1_3.OFP_VERSION != 4 or not callable(ofproto_v1_3_parser.OFPHello):
        raise RuntimeError("os-ken OpenFlow 1.3 imports failed")

    matplotlib.use("Agg")
    from matplotlib import pyplot
    figure, axes = pyplot.subplots()
    axes.plot([0, 1], [0, 1])
    output = io.BytesIO()
    figure.savefig(output, format="png")
    pyplot.close(figure)
    if not output.getvalue().startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("matplotlib did not render a PNG")

    switch = partial(OVSSwitch, datapath="user", failMode="standalone")
    net = Mininet(
        topo=SingleSwitchTopo(k=2), switch=switch, controller=None,
        autoSetMacs=True, build=False,
    )
    try:
        net.build()
        net.start()
        if run("ovs-vsctl", "get", "Bridge", "s1", "datapath_type") != "netdev":
            raise RuntimeError("Expected the OVS userspace datapath")
        if "netdev@" not in run("ovs-appctl", "dpif/show"):
            raise RuntimeError("OVS did not create a userspace datapath")
        for host in net.hosts:
            # netdev does not complete the partial TX checksums from veth.
            run("mnexec", "-a", str(host.pid), "ethtool", "-K",
                host.defaultIntf().name, "tx", "off", "tso", "off",
                "gso", "off", "gro", "off")
        loss = net.pingAll(timeout="3")
        if loss != 0:
            raise RuntimeError(f"Mininet ping loss: {loss}%")

        client, server_host = net.hosts
        server = server_host.popen(
            ["iperf3", "--server", "--one-off"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                if server.poll() is not None:
                    raise RuntimeError(f"iperf3 server exited: {server.communicate()[0]}")
                listeners = run("mnexec", "-a", str(server_host.pid),
                                "ss", "-H", "-ltn", "sport", "=", ":5201")
                if listeners:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("iperf3 server did not listen within 10 seconds")
                time.sleep(0.1)
            result = json.loads(run(
                "mnexec", "-a", str(client.pid), "iperf3",
                "--client", server_host.IP(), "--time", "2",
                "--connect-timeout", "3000", "--json",
            ))
            if "error" in result:
                raise RuntimeError(result["error"])
            received = result["end"]["sum_received"]["bytes"]
            if received <= 0:
                raise RuntimeError("iperf3 received no TCP data")
            server_output, _ = server.communicate(timeout=10)
            print(server_output, end="", flush=True)
            if server.returncode != 0:
                raise RuntimeError(f"iperf3 server returned {server.returncode}")
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.communicate()
        print("SMOKE_RESULT", json.dumps({
            "architecture": actual_arch, "ping_loss_percent": loss,
            "tcp_received_bytes": received, "status": "PASS",
        }), flush=True)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
