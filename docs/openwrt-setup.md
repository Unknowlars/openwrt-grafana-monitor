# OpenWRT Router Setup

This guide walks through everything needed on the router side.

## Requirements

- OpenWRT 21.02 or newer (22.03+ recommended)
- At least 8 MB flash free (check with `df -h`)
- At least 32 MB RAM free (check with `free`)
- SSH access to the router

## Quick setup (script)

```sh
# From your local machine:
scp openwrt/setup.sh root@192.168.0.1:/tmp/
ssh root@192.168.0.1 "sh /tmp/setup.sh 192.168.0.100"
#                                        ^^^^^^^^^^^^ IP of monitoring host
```

That's it. The script handles everything below automatically.

---

## Manual setup

### 1. Install prometheus-node-exporter-lua

```sh
opkg update
opkg install \
  prometheus-node-exporter-lua \
  prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-wifi \
  prometheus-node-exporter-lua-wifi_stations \
  prometheus-node-exporter-lua-nat_traffic \
  prometheus-node-exporter-lua-netstat
```

Enable and start:

```sh
/etc/init.d/prometheus-node-exporter-lua enable
/etc/init.d/prometheus-node-exporter-lua start
```

Verify it's working:

```sh
curl http://127.0.0.1:9100/metrics | head -40
```

You should see lines like:
```
node_memory_MemTotal_bytes 134217728
node_load1 0.05
wifi_stations_associated{ifname="wlan0"} 3
```

### 2. Configure remote syslog

Replace `192.168.0.100` with the IP of the machine running Docker:

```sh
uci set system.@system[0].log_ip=192.168.0.100
uci set system.@system[0].log_port=514
uci set system.@system[0].log_proto=udp
uci commit system
/etc/init.d/log restart
```

Verify logs are flowing (on the monitoring host):

```sh
# Should show OpenWRT syslog lines:
docker logs alloy 2>&1 | grep -i syslog
```

### 3. Optional: MWAN3 metrics

If you use mwan3 for multi-WAN failover:

```sh
opkg install prometheus-node-exporter-lua-mwan3
```

The exporter picks it up automatically — no restart needed.

### 4. Optional: Enable firewall logging

To see firewall DROP events in the Logs dashboard, enable logging in `/etc/config/firewall`:

```sh
# Log all forwarded traffic that gets dropped:
uci set firewall.@defaults[0].drop_invalid=1
uci commit firewall
/etc/init.d/firewall restart
```

Or add `option log 1` to specific rules in `/etc/config/firewall`.

---

## Checking flash/RAM usage

Before installing, check available space:

```sh
df -h /overlay    # Flash space
free              # RAM
```

Typical package sizes:
- `prometheus-node-exporter-lua` base: ~20 KB
- Each collector module: ~5-15 KB
- Total for all recommended modules: ~100-150 KB

---

## Verifying the setup

After setup, confirm from the router:

```sh
# Metrics endpoint up?
curl -s http://127.0.0.1:9100/metrics | grep "^node_" | head -5

# Syslog configured?
uci show system | grep log_

# Exporter service running?
/etc/init.d/prometheus-node-exporter-lua status
```

From the monitoring host:

```sh
# Can we scrape the router?
curl http://192.168.0.1:9100/metrics | head -5

# Are logs arriving in Alloy?
docker logs alloy --tail 20
```

---

## WAN interface name

The WAN interface name varies by router. Common names:
- `eth0` or `eth1` (most routers)
- `pppoe-wan` (PPPoE connections)
- `wwan0` (4G/LTE routers)

Check yours:

```sh
ip route | grep default
# or
cat /proc/net/dev | grep -v "lo\|br\|wlan"
```

Update the WAN interface in the dashboard panels if needed. The default dashboards use `eth0`.
