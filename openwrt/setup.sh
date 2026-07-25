#!/bin/sh
# =============================================================================
# OpenWrt Grafana Monitor — Router Setup Script
# =============================================================================
#
# Run this script ON your OpenWrt router via SSH.
# It expects the whole `openwrt/` directory so it can copy the bundled
# collector files and helper scripts alongside the setup script.
#
#   scp -O -r openwrt root@192.168.0.1:/tmp/
#   ssh root@192.168.0.1 "sh /tmp/openwrt/setup.sh <MONITORING_HOST_IP>"
#
# Arguments:
#   $1  IP address of the machine running the Docker monitoring stack
#       (required — the router will send syslog here)
#
# Optional environment variables:
#   EXPORTER_LISTEN_INTERFACE  Interface for :9100; default: lan
#   SYSLOG_PORT                Remote syslog port; default: 514
#   SYSLOG_PROTO               Remote syslog protocol; default: udp
#   PING_TARGET                Packet-loss and WAN internet probe target; default: 1.1.1.1
#   DNS_PROBE_HOST             DNS resolution probe host; default: openwrt.org
#   DNS_PROBE_TIMEOUT          DNS probe ping fallback timeout; default: 5
#   OPENWRT_MONITOR_PROFILE    core|traffic|wifi_mesh|dpi|clients|netflow|full,
#                              or a comma-separated list of the non-full names
#                              (e.g. "traffic,wifi_mesh"); default: core.
#                              "full" enables everything and cannot be
#                              combined with other names.
#   TRAFFIC_LAN_INTERFACE      LAN bridge for per-device nftables counters; default: br-lan
#   NETFLOW_PORT               UDP port the Akvorado inlet listens on for
#                              NetFlow; default: 2055. Only used by the
#                              `netflow` profile.
#   NETFLOW_INTERFACES         Space- or comma-separated devices softflowd
#                              captures on; default: TRAFFIC_LAN_INTERFACE
#                              (br-lan). One softflowd instance per device.
#                              The LAN bridge, NOT the WAN device: traffic on
#                              WAN is already SNATed, so every outbound flow
#                              would carry the router's public address as its
#                              source and per-client attribution would be lost
#                              entirely. Capturing on the bridge sees pre-NAT
#                              addresses. Do not capture both -- each packet
#                              would be counted twice.
#   NETFLOW_SAMPLING_RATE      softflowd `-s`; default: 1 (capture every packet).
#                              The stock OpenWrt UCI default is 100. If you
#                              raise this, raise `default-sampling-rate` in
#                              akvorado/akvorado.yaml to match or byte counts
#                              read low by exactly this factor.
#   NETFLOW_TIMEOUTS           softflowd `-t`; default: maxlife=60. softflowd's
#                              own defaults are tcp/general 1h and maxlife 1
#                              WEEK, and a flow is only exported when it
#                              expires -- so without this an ongoing transfer
#                              shows up as nothing at all until it ends, then
#                              as one spike stamped at expiry time. The stock
#                              init script maps exactly one -t.
#   NETFLOW_MAX_FLOWS          softflowd flow-table cap; default: 8192. Overflow
#                              silently force-expires flows; watch the
#                              openwrt_netflow_flows_dropped_total metric.
#   NETFLOW_DISABLE_HW_OFFLOAD 0|1; default: 0 (leave offload alone). softflowd
#                              captures via libpcap, so traffic forwarded by the
#                              switch ASIC under hardware flow offload is
#                              INVISIBLE to it and flow data is incomplete.
#                              Setting 1 turns hardware offload off, which makes
#                              accounting complete at the cost of routing
#                              throughput. Left off by default because that is
#                              an operator's call, not a monitoring tool's; the
#                              dashboard shows the incomplete state either way.
#   CLIENT_INVENTORY_MAX       Cap on distinct clients the `clients` profile's
#                              inventory collector will export per scrape (and
#                              on how many first-seen records it retains
#                              across scrapes, LRU-evicted); default: 256
#   CRON_LOG_LEVEL             busybox crond log level; default: 9 (suppresses the
#                              per-job-start lines this setup would otherwise send
#                              to syslog at ERROR severity). Use 8 to restore them.
#   DNS_QUERY_LOGGING          0|1; default: 0 (off). When 1, enables dnsmasq
#                              per-client DNS query logging to syslog/Loki --
#                              this is a full household browsing history for as
#                              long as Loki retains logs. Opt-in only; see
#                              docs/client-topology-and-netflow-plan.md §4.1.
#
# =============================================================================

set -eu

MONITORING_HOST="${1:-}"
EXPORTER_LISTEN_INTERFACE="${EXPORTER_LISTEN_INTERFACE:-lan}"
SYSLOG_PORT="${SYSLOG_PORT:-514}"
SYSLOG_PROTO="${SYSLOG_PROTO:-udp}"
PING_TARGET="${PING_TARGET:-1.1.1.1}"
DNS_PROBE_HOST="${DNS_PROBE_HOST:-openwrt.org}"
DNS_PROBE_TIMEOUT="${DNS_PROBE_TIMEOUT:-5}"
OPENWRT_MONITOR_PROFILE="${OPENWRT_MONITOR_PROFILE:-core}"
TRAFFIC_LAN_INTERFACE="${TRAFFIC_LAN_INTERFACE:-br-lan}"
CLIENT_INVENTORY_MAX="${CLIENT_INVENTORY_MAX:-256}"
NETFLOW_PORT="${NETFLOW_PORT:-2055}"
NETFLOW_INTERFACES="${NETFLOW_INTERFACES:-}"
NETFLOW_SAMPLING_RATE="${NETFLOW_SAMPLING_RATE:-1}"
NETFLOW_TIMEOUTS="${NETFLOW_TIMEOUTS:-maxlife=60}"
NETFLOW_MAX_FLOWS="${NETFLOW_MAX_FLOWS:-8192}"
NETFLOW_DISABLE_HW_OFFLOAD="${NETFLOW_DISABLE_HW_OFFLOAD:-0}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COLLECTOR_SRC_DIR="$SCRIPT_DIR/collectors"
HELPER_SRC_DIR="$SCRIPT_DIR/scripts"
# Shared Lua modules, installed to /usr/lib/lua/ rather than the collectors
# directory: the exporter turns every file it finds there into a collector and
# calls scrape() on it.
LUA_SRC_DIR="$SCRIPT_DIR/lua"
CRONTAB_FILE="/etc/crontabs/root"

log() {
  printf '%s\n' "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

pkg_update() {
  case "$PKG_MANAGER" in
    apk) apk update ;;
    opkg) opkg update ;;
  esac
}

pkg_install_required() {
  case "$PKG_MANAGER" in
    apk) apk add "$@" ;;
    opkg) opkg install "$@" ;;
  esac
}

pkg_install_optional() {
  case "$PKG_MANAGER" in
    apk) apk add "$@" ;;
    opkg) opkg install "$@" ;;
  esac
}

install_conntrack_cli() {
  case "$PKG_MANAGER" in
    # OpenWrt 25.12/APK ships the CLI as `conntrack`; trying the older
    # `conntrack-tools` name first prints a scary "no such package" error even
    # though the fallback succeeds.
    apk) pkg_install_optional conntrack ;;
    opkg) pkg_install_optional conntrack-tools || pkg_install_optional conntrack ;;
  esac
}

pkg_installed() {
  case "$PKG_MANAGER" in
    apk) apk info "$1" >/dev/null 2>&1 ;;
    opkg) opkg list-installed 2>/dev/null | grep -q "^$1 " ;;
  esac
}

profile_enabled() {
  # OPENWRT_MONITOR_PROFILE is validated as a comma-separated list of known
  # profile names (or the bare word "full") before this is ever called; see
  # the validating `case` below. Wrapping in commas turns membership into a
  # plain substring check that `case` globbing can do without forking to
  # grep/awk, which OpenWrt's ash does not need for a handful of short words.
  profile="$1"
  case ",$OPENWRT_MONITOR_PROFILE," in
    *,full,*) return 0 ;;
    *",$profile,"*) return 0 ;;
    *) return 1 ;;
  esac
}

fetch_url() {
  url="$1"
  if command -v wget >/dev/null 2>&1; then
    wget -qO- "$url"
  elif command -v curl >/dev/null 2>&1; then
    curl -fsS "$url"
  else
    return 1
  fi
}

ensure_cron_line() {
  line="$1"

  touch "$CRONTAB_FILE"
  if ! grep -Fqx "$line" "$CRONTAB_FILE"; then
    printf '%s\n' "$line" >> "$CRONTAB_FILE"
  fi
}

ensure_dir() {
  dir="$1"

  mkdir -p "$dir"
}

install_file() {
  src="$1"
  dest="$2"
  mode="$3"

  cp "$src" "$dest"
  chmod "$mode" "$dest"
}

# ── Validate ──────────────────────────────────────────────────────────────────

REQUIRED_PACKAGES="
prometheus-node-exporter-lua
prometheus-node-exporter-lua-textfile
prometheus-node-exporter-lua-uci_dhcp_host
prometheus-node-exporter-lua-openwrt
prometheus-node-exporter-lua-nat_traffic
prometheus-node-exporter-lua-netstat
"

OPTIONAL_PACKAGES="
prometheus-node-exporter-lua-wifi
prometheus-node-exporter-lua-wifi_stations
prometheus-node-exporter-lua-hostapd_stations
prometheus-node-exporter-lua-hwmon
prometheus-node-exporter-lua-thermal
prometheus-node-exporter-lua-nft-counters
prometheus-node-exporter-lua-snmp6
ip-bridge
"

if [ -z "$MONITORING_HOST" ]; then
  log "Usage: $0 <MONITORING_HOST_IP>"
  log "  Example: $0 192.168.0.100"
  exit 1
fi

if [ ! -d "$COLLECTOR_SRC_DIR" ] || [ ! -d "$HELPER_SRC_DIR" ] || [ ! -d "$LUA_SRC_DIR" ]; then
  die "setup.sh expects the whole openwrt/ directory. Copy it with: scp -O -r openwrt root@<router>:/tmp/"
fi

if command -v apk >/dev/null 2>&1; then
  PKG_MANAGER="apk"
elif command -v opkg >/dev/null 2>&1; then
  PKG_MANAGER="opkg"
else
  die "neither apk nor opkg was found. This script supports OpenWrt 24.10/opkg and OpenWrt 25.12/apk."
fi

case "$SYSLOG_PROTO" in
  udp|tcp) ;;
  *) die "SYSLOG_PROTO must be udp or tcp" ;;
esac

# OPENWRT_MONITOR_PROFILE accepts a single name or a comma-separated list of
# names (e.g. "traffic,wifi_mesh"). "full" is an alias for all profiles and
# is validated the same way a single name would be — profile_enabled() above
# already treats it specially, so "full,traffic" would be redundant but not
# wrong; it is rejected here anyway to keep the input one unambiguous shape.
case "$OPENWRT_MONITOR_PROFILE" in
  *[!A-Za-z0-9_,]*|''|*,|,*|*,,*)
    die "OPENWRT_MONITOR_PROFILE must be a comma-separated list of: core, traffic, wifi_mesh, dpi, clients, netflow (or the single word full)" ;;
esac
OLD_IFS=$IFS
IFS=,
for _profile_token in $OPENWRT_MONITOR_PROFILE; do
  case "$_profile_token" in
    core|traffic|wifi_mesh|dpi|clients|netflow) ;;
    full)
      if [ "$OPENWRT_MONITOR_PROFILE" != "full" ]; then
        IFS=$OLD_IFS
        die "OPENWRT_MONITOR_PROFILE: full cannot be combined with other profile names"
      fi
      ;;
    *)
      IFS=$OLD_IFS
      die "OPENWRT_MONITOR_PROFILE must be a comma-separated list of: core, traffic, wifi_mesh, dpi, clients, netflow (or the single word full) — got '$_profile_token'"
      ;;
  esac
done
IFS=$OLD_IFS

case "$CLIENT_INVENTORY_MAX" in
  ''|*[!0-9]*|0) die "CLIENT_INVENTORY_MAX must be a positive integer" ;;
esac

if profile_enabled netflow; then
  NETFLOW_INTERFACE_SPECS=""
  case "$NETFLOW_PORT" in
    ''|*[!0-9]*|0) die "NETFLOW_PORT must be a positive integer" ;;
  esac
  case "$NETFLOW_SAMPLING_RATE" in
    ''|*[!0-9]*|0) die "NETFLOW_SAMPLING_RATE must be a positive integer (1 = capture every packet)" ;;
  esac
  case "$NETFLOW_MAX_FLOWS" in
    ''|*[!0-9]*|0) die "NETFLOW_MAX_FLOWS must be a positive integer" ;;
  esac
  # Substituted into a UCI value and then into a softflowd -t argument. Allow
  # only what a timeout spec can contain, and reject the quote characters that
  # would let it break out of the single-quoted option.
  case "$NETFLOW_TIMEOUTS" in
    ''|*[!A-Za-z0-9=.,_-]*) die "NETFLOW_TIMEOUTS must be a softflowd timeout spec such as maxlife=60" ;;
  esac
  case "$NETFLOW_DISABLE_HW_OFFLOAD" in
    0|1) ;;
    *) die "NETFLOW_DISABLE_HW_OFFLOAD must be 0 or 1" ;;
  esac

  # Commas are accepted for symmetry with OPENWRT_MONITOR_PROFILE and
  # ROUTER_TARGETS; softflowd config generation below iterates on whitespace.
  NETFLOW_INTERFACES=$(printf '%s' "$NETFLOW_INTERFACES" | tr ',' ' ')
  if [ -z "$(printf '%s' "$NETFLOW_INTERFACES" | tr -d ' ')" ]; then
    # The LAN bridge, deliberately, not the WAN device. softflowd captures with
    # libpcap at the device, and on WAN that is *after* SNAT: every outbound
    # flow would carry the router's public address as its source, so "which
    # client is using the bandwidth" -- the whole point of per-flow data on a
    # home network -- would be unanswerable. The bridge sees pre-NAT addresses.
    NETFLOW_INTERFACES="$TRAFFIC_LAN_INTERFACE"
  fi
  for _iface in $NETFLOW_INTERFACES; do
    # The device name is substituted into /etc/config/softflowd, into pid and
    # control-socket paths, and into a pcap filter expression. Restrict it to
    # what a Linux netdev name can actually contain.
    case "$_iface" in
      *[!A-Za-z0-9_.-]*|'') die "NETFLOW_INTERFACES entries must contain only letters, numbers, dots, underscores, or hyphens — got '$_iface'" ;;
    esac
    [ -e "/sys/class/net/$_iface" ] || die "NETFLOW_INTERFACES names '$_iface', which is not a network device on this router. softflowd would fail to start."
    read -r _ifindex < "/sys/class/net/$_iface/ifindex" || die "cannot read ifIndex for NETFLOW_INTERFACES entry '$_iface'"
    case "$_ifindex" in
      ''|*[!0-9]*) die "invalid ifIndex for NETFLOW_INTERFACES entry '$_iface': $_ifindex" ;;
    esac
    NETFLOW_INTERFACE_SPECS="$NETFLOW_INTERFACE_SPECS $_ifindex:$_iface"
  done
fi

log "==> OpenWrt Grafana Monitor setup"
log "    Monitoring host: $MONITORING_HOST"
log "    Package manager: $PKG_MANAGER"
log "    Exporter interface: $EXPORTER_LISTEN_INTERFACE"
log "    Syslog: $MONITORING_HOST:$SYSLOG_PORT/$SYSLOG_PROTO"
log "    Monitoring profile: $OPENWRT_MONITOR_PROFILE"
if profile_enabled netflow; then
  log "    NetFlow: $NETFLOW_INTERFACES -> $MONITORING_HOST:$NETFLOW_PORT (v9, sampling 1:$NETFLOW_SAMPLING_RATE, -t $NETFLOW_TIMEOUTS)"
fi
log ""

# ── Install packages ──────────────────────────────────────────────────────────

log "==> Updating package list..."
pkg_update

log "==> Installing required Prometheus exporters..."
pkg_install_required $REQUIRED_PACKAGES

if profile_enabled traffic || profile_enabled dpi; then
  log "==> Installing JSON collector dependencies..."
  if ! pkg_install_optional lua-cjson; then
    log "    WARNING: lua-cjson is unavailable; JSON-based optional metrics will remain unavailable"
  fi
fi

if profile_enabled traffic; then
  log "==> Installing nftables traffic dependencies..."
  if ! pkg_install_optional nftables-json; then
    log "    WARNING: nftables-json is unavailable; per-device traffic metrics will remain unavailable"
  fi
fi

if profile_enabled netflow; then
  # softflowd is the only NetFlow exporter in the stock OpenWrt feeds (pmacct,
  # ipt-netflow, ipfixprobe and nprobe are all absent). It pulls in libpcap.
  log "==> Installing NetFlow exporter (softflowd)..."
  if ! pkg_install_optional softflowd; then
    log "    WARNING: softflowd is unavailable; NetFlow export will remain unavailable"
  fi
fi

if profile_enabled dpi; then
  log "==> Installing DPI collector dependencies..."
  if ! pkg_install_optional netifyd; then
    log "    WARNING: netifyd is unavailable; the DPI collector will report unavailable until Netifyd is installed"
  fi
fi

if profile_enabled wifi_mesh; then
  log "==> Installing WiFi mesh collector dependencies..."
  if ! pkg_install_optional libuci-lua; then
    log "    WARNING: libuci-lua is unavailable; usteer configuration metrics will be unavailable"
  fi
  if ! pkg_install_optional luci-lib-nixio; then
    log "    WARNING: luci-lib-nixio is unavailable; WiFi mesh collector may be unavailable"
  fi
fi

if profile_enabled clients; then
  log "==> Installing client inventory and traffic collector dependencies..."
  # rpcd-mod-luci provides the luci-rpc ubus object (getHostHints). Its
  # absence is not fatal: the collector falls back to /tmp/dhcp.leases, at
  # the cost of AP/SSID/band attribution -- see docs/advanced-profiles.md.
  if ! pkg_install_optional rpcd-mod-luci; then
    log "    WARNING: rpcd-mod-luci is unavailable; client inventory will fall back to the DHCP leasefile with no AP/SSID/band attribution"
  fi
  if ! pkg_install_optional libubus-lua; then
    log "    WARNING: libubus-lua is unavailable; client inventory will fall back to the DHCP leasefile"
  fi
  if ! pkg_install_optional libiwinfo-lua; then
    log "    WARNING: libiwinfo-lua is unavailable; client inventory will have no AP/SSID/band attribution"
  fi
  if ! pkg_install_optional libuci-lua; then
    log "    WARNING: libuci-lua is unavailable; client inventory will have no network/static-lease/offload attribution"
  fi
  if ! pkg_install_optional nlbwmon; then
    log "    WARNING: nlbwmon is unavailable; per-client traffic accounting will report unavailable"
  fi
  # openwrt-monitor-client-conntrack.sh prefers the `conntrack` CLI and falls
  # back to procfs conntrack rows when available. Package names vary across
  # OpenWrt feeds; select the quiet known name first for the active package
  # manager before accepting degraded conntrack-only availability.
  if ! install_conntrack_cli; then
    log "    WARNING: conntrack CLI packages are unavailable; per-client conntrack counts will use procfs when available or report unavailable"
  fi
fi

log "==> Installing inode helper dependency..."
if ! df -iP / >/dev/null 2>&1 && ! command -v stat >/dev/null 2>&1; then
  if ! pkg_install_optional coreutils-stat; then
    log "    WARNING: coreutils-stat is unavailable; inode metrics will report unavailable if df lacks -i support"
  fi
fi

for package in $OPTIONAL_PACKAGES; do
  log "==> Installing optional package: $package"
  if ! pkg_install_optional "$package"; then
    log "    WARNING: optional package unavailable or failed to install: $package"
  fi
done

# Install mwan3 exporter only if mwan3 is installed
if pkg_installed mwan3; then
  log "==> mwan3 detected, installing mwan3 exporter..."
  if ! pkg_install_optional prometheus-node-exporter-lua-mwan3; then
    log "    WARNING: mwan3 exporter unavailable or failed to install"
  fi
fi

# ── Install bundled helper files ───────────────────────────────────────────────

log "==> Installing bundled collector files and helper scripts..."
ensure_dir /usr/lib/lua/prometheus-collectors
ensure_dir /usr/bin
ensure_dir /var/prometheus

cat >/etc/openwrt-grafana-monitor.conf <<EOF
PING_TARGET="$PING_TARGET"
WAN_PROBE_TARGET="$PING_TARGET"
DNS_PROBE_HOST="$DNS_PROBE_HOST"
DNS_PROBE_TIMEOUT="$DNS_PROBE_TIMEOUT"
OPENWRT_MONITOR_PROFILE="$OPENWRT_MONITOR_PROFILE"
TRAFFIC_LAN_INTERFACE="$TRAFFIC_LAN_INTERFACE"
CLIENT_INVENTORY_MAX="$CLIENT_INVENTORY_MAX"
NETFLOW_INTERFACES="$NETFLOW_INTERFACES"
NETFLOW_PORT="$NETFLOW_PORT"
EOF

install_file "$COLLECTOR_SRC_DIR/dnsmasq.lua" /usr/lib/lua/prometheus-collectors/dnsmasq.lua 0644
install_file "$COLLECTOR_SRC_DIR/device_status.lua" /usr/lib/lua/prometheus-collectors/device_status.lua 0644
install_file "$COLLECTOR_SRC_DIR/packet_loss.lua" /usr/lib/lua/prometheus-collectors/packet_loss.lua 0644
install_file "$COLLECTOR_SRC_DIR/wan_info.lua" /usr/lib/lua/prometheus-collectors/wan_info.lua 0644

if profile_enabled traffic; then
  ensure_dir /etc/nftables.d
  install_file "$COLLECTOR_SRC_DIR/device_traffic.lua" /usr/lib/lua/prometheus-collectors/device_traffic.lua 0644
  case "$TRAFFIC_LAN_INTERFACE" in
    *[!A-Za-z0-9_.-]*|'') die "TRAFFIC_LAN_INTERFACE must contain only letters, numbers, dots, underscores, or hyphens" ;;
  esac
  sed "s/__LAN_INTERFACE__/$TRAFFIC_LAN_INTERFACE/g" \
    "$SCRIPT_DIR/nftables/openwrt-device-traffic.nft" > /etc/nftables.d/openwrt-device-traffic.nft
fi

if profile_enabled netflow; then
  install_file "$HELPER_SRC_DIR/openwrt-monitor-netflow-health.sh" /usr/bin/openwrt-monitor-netflow-health.sh 0755

  # Rendered wholesale rather than merged: /etc/config/softflowd is entirely
  # owned by this profile, and the interface list can shrink between runs, so
  # appending would leave orphaned sections exporting from devices the operator
  # removed. Staged next to the target because /tmp is a separate filesystem on
  # OpenWrt and an mv across it would not be atomic.
  log "==> Writing /etc/config/softflowd for: $NETFLOW_INTERFACES"
  : > /etc/config/softflowd.new
  for netflow_spec in $NETFLOW_INTERFACE_SPECS; do
    netflow_ifindex=${netflow_spec%%:*}
    netflow_iface=${netflow_spec#*:}
    sed -e "s/__IFINDEX__/$netflow_ifindex/g" \
        -e "s/__IFACE__/$netflow_iface/g" \
        -e "s/__COLLECTOR_HOST__/$MONITORING_HOST/g" \
        -e "s/__COLLECTOR_PORT__/$NETFLOW_PORT/g" \
        -e "s/__SAMPLING_RATE__/$NETFLOW_SAMPLING_RATE/g" \
        -e "s/__MAX_FLOWS__/$NETFLOW_MAX_FLOWS/g" \
        -e "s/__TIMEOUTS__/$NETFLOW_TIMEOUTS/g" \
        "$SCRIPT_DIR/netflow/softflowd.config" >> /etc/config/softflowd.new
    printf '\n' >> /etc/config/softflowd.new
  done
  mv /etc/config/softflowd.new /etc/config/softflowd

  if [ "$NETFLOW_DISABLE_HW_OFFLOAD" = "1" ]; then
    # Opt-in only. softflowd captures via libpcap; packets forwarded by the
    # switch ASIC never reach the CPU, so they are invisible to it. Turning
    # hardware offload off makes flow accounting complete at the cost of
    # routing throughput on this hardware.
    log "==> Disabling hardware flow offload so softflowd can see forwarded traffic..."
    uci set firewall.@defaults[0].flow_offloading_hw='0'
    uci commit firewall
  else
    log "    NOTE: hardware flow offload left as configured. If it is on, traffic"
    log "          forwarded by the switch ASIC is invisible to softflowd and flow"
    log "          data is incomplete. The dashboard's NetFlow health tab shows this"
    log "          state explicitly. Set NETFLOW_DISABLE_HW_OFFLOAD=1 to turn it off."
  fi
fi

if profile_enabled wifi_mesh; then
  install_file "$COLLECTOR_SRC_DIR/wifi_dethrash.lua" /usr/lib/lua/prometheus-collectors/wifi_dethrash.lua 0644
fi

if profile_enabled dpi; then
  install_file "$COLLECTOR_SRC_DIR/dpi_netifyd.lua" /usr/lib/lua/prometheus-collectors/dpi_netifyd.lua 0644
fi

if profile_enabled clients; then
  install_file "$COLLECTOR_SRC_DIR/client_inventory.lua" /usr/lib/lua/prometheus-collectors/client_inventory.lua 0644
  install_file "$HELPER_SRC_DIR/openwrt-monitor-client-traffic.sh" /usr/bin/openwrt-monitor-client-traffic.sh 0755
  install_file "$HELPER_SRC_DIR/openwrt-monitor-client-conntrack.sh" /usr/bin/openwrt-monitor-client-conntrack.sh 0755
  # topology.lua reshapes the same identity/association data client_inventory
  # gathers into the node-graph metric contract (plan §2.2-§2.4); it has the
  # same package dependencies (getHostHints, iwinfo assoclist), so it rides
  # along in the same profile rather than getting its own.
  install_file "$COLLECTOR_SRC_DIR/topology.lua" /usr/lib/lua/prometheus-collectors/topology.lua 0644
  # topology.lua require()s these to name a client by its hardware vendor
  # instead of by its MAC. They go in /usr/lib/lua/, not the collectors
  # directory, because anything in there is loaded as a collector and these
  # have no scrape(). Absent, oui.lookup() degrades to "no vendor" and the
  # graph still renders -- see openwrt/lua/oui.lua.
  install_file "$LUA_SRC_DIR/oui.lua" /usr/lib/lua/openwrt_oui.lua 0644
  install_file "$LUA_SRC_DIR/oui_data.lua" /usr/lib/lua/openwrt_oui_data.lua 0644
  if [ -x /etc/init.d/nlbwmon ]; then
    ensure_dir /usr/share/nlbwmon
    install_file "$SCRIPT_DIR/nlbwmon/protocols" /usr/share/nlbwmon/protocols 0644
    log "==> Restarting nlbwmon to load the trimmed service buckets..."
    /etc/init.d/nlbwmon enable
    /etc/init.d/nlbwmon restart
  else
    log "    WARNING: nlbwmon is not installed; skipping protocols file and service restart"
  fi
fi

install_file "$HELPER_SRC_DIR/openwrt-monitor-device-status.sh" /usr/bin/openwrt-monitor-device-status.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-packet-loss.sh" /usr/bin/openwrt-monitor-packet-loss.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-wan-info.sh" /usr/bin/openwrt-monitor-wan-info.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-filesystem.sh" /usr/bin/openwrt-monitor-filesystem.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-service-health.sh" /usr/bin/openwrt-monitor-service-health.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-wan-quality.sh" /usr/bin/openwrt-monitor-wan-quality.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-dhcp-pool.sh" /usr/bin/openwrt-monitor-dhcp-pool.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-link-health.sh" /usr/bin/openwrt-monitor-link-health.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-softnet.sh" /usr/bin/openwrt-monitor-softnet.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-ipv6-health.sh" /usr/bin/openwrt-monitor-ipv6-health.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-inodes.sh" /usr/bin/openwrt-monitor-inodes.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-firewall-counters.sh" /usr/bin/openwrt-monitor-firewall-counters.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-sqm.sh" /usr/bin/openwrt-monitor-sqm.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-wifi-radio.sh" /usr/bin/openwrt-monitor-wifi-radio.sh 0755

# ── Remove superseded collectors ──────────────────────────────────────────────
#
# Versions before the helper-script split installed two monolithic collectors,
# openwrt-grafana-monitor-metrics and openwrt-grafana-monitor-sqm, on a
# once-a-minute schedule. Every metric they emit (overlay_bytes_*,
# dhcpv6_lease_count, gateway_packet_loss, dns_probe_*, wan_public_ip_changed,
# openwrt_wifi_station_connected_seconds) is now emitted by the split helpers
# above as a compatibility alias, so leaving the old scripts scheduled exposes
# each of those series twice. Prometheus keeps the first sample, silently drops
# the second and reports no scrape error, which makes the duplication invisible
# from the dashboard. The old setup script removed its own cron entries on
# rerun; that logic was lost in the split, so do it explicitly here.

log "==> Removing superseded collectors from earlier versions..."
for legacy_collector in \
  /usr/bin/openwrt-grafana-monitor-metrics \
  /usr/bin/openwrt-grafana-monitor-sqm
do
  if [ -e "$legacy_collector" ]; then
    rm -f "$legacy_collector"
    log "    removed $legacy_collector"
  fi
done

if grep -qE 'openwrt-grafana-monitor-(metrics|sqm)' "$CRONTAB_FILE" 2>/dev/null; then
  # grep -v exits 1 when it selects no lines. On a router whose crontab holds
  # *only* the two legacy entries that is the expected result, not an error, so
  # without `|| true` this aborts the whole install under `set -e`. An empty
  # .clean file is correct here: the cron-install block below repopulates it.
  # Staged alongside the crontab on purpose -- /tmp is a separate filesystem on
  # OpenWrt, so an mv across would not be atomic.
  grep -vE 'openwrt-grafana-monitor-(metrics|sqm)' "$CRONTAB_FILE" \
    > "$CRONTAB_FILE.clean" || true
  mv "$CRONTAB_FILE.clean" "$CRONTAB_FILE"
  log "    removed superseded cron entries"
fi
rm -f "$CRONTAB_FILE.clean"

# /var/prometheus holds only derived data and every current helper is run once
# below, so clearing it is safe and drops output files left behind by collectors
# that no longer exist.
rm -f /var/prometheus/*.prom

# ── Configure exporter listener ────────────────────────────────────────────────

log "==> Configuring exporter listener on $EXPORTER_LISTEN_INTERFACE..."
uci set prometheus-node-exporter-lua.main.listen_interface="$EXPORTER_LISTEN_INTERFACE"
uci set prometheus-node-exporter-lua.main.listen_port='9100'
uci commit prometheus-node-exporter-lua

# ── Configure scheduled helper scripts ─────────────────────────────────────────

log "==> Configuring helper cron jobs..."
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-device-status.sh'
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-service-health.sh'
ensure_cron_line '*/5 * * * * /usr/bin/openwrt-monitor-packet-loss.sh'
ensure_cron_line '*/5 * * * * /usr/bin/openwrt-monitor-wan-info.sh'
ensure_cron_line '*/5 * * * * /usr/bin/openwrt-monitor-wan-quality.sh'
ensure_cron_line '*/10 * * * * /usr/bin/openwrt-monitor-filesystem.sh'
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-dhcp-pool.sh'
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-link-health.sh'
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-softnet.sh'
ensure_cron_line '*/5 * * * * /usr/bin/openwrt-monitor-ipv6-health.sh'
ensure_cron_line '*/10 * * * * /usr/bin/openwrt-monitor-inodes.sh'
ensure_cron_line '*/2 * * * * /usr/bin/openwrt-monitor-firewall-counters.sh'
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-sqm.sh'
ensure_cron_line '*/2 * * * * /usr/bin/openwrt-monitor-wifi-radio.sh'
if profile_enabled clients; then
  ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-client-traffic.sh'
  ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-client-conntrack.sh'
fi
if profile_enabled netflow; then
  ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-netflow-health.sh'
fi

log "==> Running helper scripts once so custom metrics appear immediately..."
/usr/bin/openwrt-monitor-device-status.sh
/usr/bin/openwrt-monitor-service-health.sh
/usr/bin/openwrt-monitor-packet-loss.sh
/usr/bin/openwrt-monitor-wan-info.sh
/usr/bin/openwrt-monitor-wan-quality.sh
/usr/bin/openwrt-monitor-filesystem.sh
/usr/bin/openwrt-monitor-dhcp-pool.sh
/usr/bin/openwrt-monitor-link-health.sh
/usr/bin/openwrt-monitor-softnet.sh
/usr/bin/openwrt-monitor-ipv6-health.sh
/usr/bin/openwrt-monitor-inodes.sh
/usr/bin/openwrt-monitor-firewall-counters.sh
/usr/bin/openwrt-monitor-sqm.sh
/usr/bin/openwrt-monitor-wifi-radio.sh
if profile_enabled clients; then
  /usr/bin/openwrt-monitor-client-traffic.sh
  /usr/bin/openwrt-monitor-client-conntrack.sh
fi

if profile_enabled traffic; then
  log "==> Loading nftables device traffic rules..."
  /etc/init.d/firewall restart
fi

if profile_enabled netflow; then
  if [ -x /etc/init.d/softflowd ]; then
    log "==> Enabling and starting softflowd..."
    /etc/init.d/softflowd enable
    /etc/init.d/softflowd restart
  else
    log "    WARNING: /etc/init.d/softflowd is missing; softflowd did not install. NetFlow export is not running."
  fi

  # The flow_offloading_hw setting was committed earlier but only takes effect
  # on a firewall reload. Skipped when the `traffic` profile is also enabled,
  # because its block above already restarted the firewall after the commit.
  if [ "$NETFLOW_DISABLE_HW_OFFLOAD" = "1" ] && ! profile_enabled traffic; then
    log "==> Reloading firewall to apply the flow-offload change..."
    /etc/init.d/firewall restart
  fi

  # Populate the health metrics immediately rather than waiting for cron, so a
  # failed exporter is visible on the first scrape instead of a minute later.
  /usr/bin/openwrt-monitor-netflow-health.sh
fi

# ── Start and enable the exporter ─────────────────────────────────────────────

log "==> Enabling and starting prometheus-node-exporter-lua..."
/etc/init.d/prometheus-node-exporter-lua enable
/etc/init.d/prometheus-node-exporter-lua restart

# This setup schedules 14 helper jobs, several of them every minute. busybox
# crond logs one line per job start via log8(), and those informational lines go
# out through bb_vinfo_msg at syslog priority LOG_ERR (see crond.c: "Warnings/
# errors use plain bb_[p]error_msg's ... ok with LOG_ERR default"). Left at the
# default level that is ~30 ERROR-severity lines per minute shipped to Loki,
# which buries genuine router errors in the logs dashboard.
#
# crond only emits the per-job line when 8 >= log_level, so level 9 suppresses
# it. Real crond warnings and errors bypass this threshold entirely and are
# still logged.
CRON_LOG_LEVEL="${CRON_LOG_LEVEL:-9}"
log "==> Setting cron log level to $CRON_LOG_LEVEL to keep job starts out of syslog..."
uci set system.@system[0].cronloglevel="$CRON_LOG_LEVEL"
uci commit system

log "==> Enabling and restarting cron..."
/etc/init.d/cron enable
/etc/init.d/cron restart

# Give it a moment to start
sleep 2

LAN_IP="$(uci get network.lan.ipaddr 2>/dev/null || printf '%s' '<ROUTER_IP>')"
LAN_IP="${LAN_IP%%/*}"
METRICS_URL="http://$LAN_IP:9100/metrics"

if fetch_url "$METRICS_URL" > /dev/null 2>&1; then
  log "    OK: metrics endpoint is up at $METRICS_URL"
else
  log "    WARNING: metrics endpoint not responding yet, check with:"
  log "    wget -qO- $METRICS_URL"
fi

# ── Configure remote syslog ───────────────────────────────────────────────────

log "==> Configuring remote syslog to $MONITORING_HOST:$SYSLOG_PORT/$SYSLOG_PROTO ..."
uci set system.@system[0].log_ip="$MONITORING_HOST"
uci set system.@system[0].log_remote='1'
uci set system.@system[0].log_port="$SYSLOG_PORT"
uci set system.@system[0].log_proto="$SYSLOG_PROTO"
uci set system.@system[0].log_hostname="$(uci get system.@system[0].hostname 2>/dev/null || echo openwrt)"
uci commit system

/etc/init.d/log restart
log "    OK: syslog configured"

# ── Optional: per-client DNS query attribution (plan §4.1, M9) ────────────────
#
# Off by default. dnsmasq's logqueries writes one syslog line per DNS query,
# prefixed with the requesting client's IP -- this repo already ships syslog
# to Loki, so enabling this is the entire cost, but the line is a household
# browsing history for as long as Loki keeps it. It must stay opt-in and
# loudly flagged; never enable it without the operator explicitly asking.
DNS_QUERY_LOGGING="${DNS_QUERY_LOGGING:-0}"
case "$DNS_QUERY_LOGGING" in
  1)
    log "==> Enabling dnsmasq DNS query logging (DNS_QUERY_LOGGING=1)..."
    log "    WARNING: every DNS lookup by every device on this network will now be"
    log "    written to syslog and shipped to Loki -- this is a household browsing"
    log "    history, retained for as long as Loki keeps logs. Disable by re-running"
    log "    this setup with DNS_QUERY_LOGGING unset or =0."
    uci set dhcp.@dnsmasq[0].logqueries='1'
    uci commit dhcp
    /etc/init.d/dnsmasq restart
    ;;
  0|"")
    uci set dhcp.@dnsmasq[0].logqueries='0'
    uci commit dhcp
    ;;
  *)
    die "DNS_QUERY_LOGGING must be 0 or 1"
    ;;
esac

# ── Summary ───────────────────────────────────────────────────────────────────

log ""
log "==> Setup complete!"
log ""
log "    Metrics:  $METRICS_URL"
log "    Syslog:   $MONITORING_HOST:$SYSLOG_PORT/$SYSLOG_PROTO"
log "    Helpers:  device status, packet loss, WAN/public IP, WAN quality, filesystem, service health"
log "              DHCP pool, link health, softnet, IPv6 WAN health, inodes, firewall counters, SQM, WiFi radio"
log "    Optional profile collectors: $OPENWRT_MONITOR_PROFILE"
log ""
log "    Now start the Docker stack on $MONITORING_HOST:"
log "    docker compose up -d"
log ""
