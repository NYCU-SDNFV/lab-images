#!/bin/sh
# Start ovsdb-server and ovs-vswitchd, then hand over to CMD.
# Do not modify this file.
set -e

mkdir -p /var/run/openvswitch /etc/openvswitch /var/log/openvswitch

if [ ! -f /etc/openvswitch/conf.db ]; then
  ovsdb-tool create /etc/openvswitch/conf.db \
    /usr/share/openvswitch/vswitch.ovsschema
fi

# ovsdb-server
if ! pidof ovsdb-server >/dev/null 2>&1; then
  ovsdb-server /etc/openvswitch/conf.db \
    --remote=punix:/var/run/openvswitch/db.sock \
    --remote=db:Open_vSwitch,Open_vSwitch,manager_options \
    --private-key=db:Open_vSwitch,SSL,private_key \
    --certificate=db:Open_vSwitch,SSL,certificate \
    --bootstrap-ca-cert=db:Open_vSwitch,SSL,ca_cert \
    --pidfile --detach --log-file
fi

ovs-vsctl --no-wait init

# ovs-vswitchd. Kernel-datapath labs (including Lab 1) require the Docker host
# to provide the openvswitch module; netdev labs do not. We do not load it here.
if ! pidof ovs-vswitchd >/dev/null 2>&1; then
  ovs-vswitchd --pidfile --detach --log-file
fi

echo "[entrypoint] ovsdb-server pid $(pidof ovsdb-server), ovs-vswitchd pid $(pidof ovs-vswitchd)"
echo "[entrypoint] $(ovs-vsctl --version | head -1)"

exec "$@"
