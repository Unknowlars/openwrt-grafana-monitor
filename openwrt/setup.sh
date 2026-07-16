#!/bin/sh
# =============================================================================
# OpenWrt Grafana Monitor - Router Setup Script
# =============================================================================
#
# Run this script ON your OpenWrt router via SSH:
#
#   scp -O openwrt/setup.sh root@192.168.0.1:/tmp/
#   ssh root@192.168.0.1 "sh /tmp/setup.sh <MONITORING_HOST_IP>"
#
# Optional environment variables:
#   EXPORTER_LISTEN_INTERFACE  Interface for :9100; default: lan
#   SYSLOG_PORT                Remote syslog port; default: 514
#   PING_TARGET                Packet-loss probe target; default: 1.1.1.1
#   DNS_PROBE_HOST             DNS resolution probe host; default: openwrt.org
#   DNS_PROBE_TIMEOUT          DNS probe ping fallback timeout; default: 5
#   PUBLIC_IP_LOOKUP           Set to 1 to enable public IP lookup; default: 0
#   PUBLIC_IP_URL              Public IP endpoint; default: https://api.ipify.org
#   PUBLIC_IP_CHECK_INTERVAL   Public IP lookup interval in seconds; default: 900
#   ENABLE_SQM_METRICS         Set to 1 to install optional SQM/cake metrics; default: 0
#   SQM_INTERFACES             Space-separated SQM/IFB interfaces; default: empty
#   CLEANUP_LEGACY_CRON        auto prompts on TTY, 1 removes, 0 keeps; default: auto
#
# =============================================================================

set -eu

MONITORING_HOST="${1:-}"
EXPORTER_LISTEN_INTERFACE="${EXPORTER_LISTEN_INTERFACE:-lan}"
SYSLOG_PORT="${SYSLOG_PORT:-514}"
PING_TARGET="${PING_TARGET:-1.1.1.1}"
DNS_PROBE_HOST="${DNS_PROBE_HOST:-openwrt.org}"
DNS_PROBE_TIMEOUT="${DNS_PROBE_TIMEOUT:-5}"
PUBLIC_IP_LOOKUP="${PUBLIC_IP_LOOKUP:-0}"
PUBLIC_IP_URL="${PUBLIC_IP_URL:-https://api.ipify.org}"
PUBLIC_IP_CHECK_INTERVAL="${PUBLIC_IP_CHECK_INTERVAL:-900}"
ENABLE_SQM_METRICS="${ENABLE_SQM_METRICS:-0}"
SQM_INTERFACES="${SQM_INTERFACES:-}"
CLEANUP_LEGACY_CRON="${CLEANUP_LEGACY_CRON:-auto}"

REQUIRED_PACKAGES="
prometheus-node-exporter-lua
prometheus-node-exporter-lua-openwrt
prometheus-node-exporter-lua-nat_traffic
prometheus-node-exporter-lua-netstat
prometheus-node-exporter-lua-textfile
"

OPTIONAL_PACKAGES="
prometheus-node-exporter-lua-wifi
prometheus-node-exporter-lua-wifi_stations
prometheus-node-exporter-lua-hostapd_stations
prometheus-node-exporter-lua-thermal
prometheus-node-exporter-lua-hwmon
prometheus-node-exporter-lua-nft-counters
prometheus-node-exporter-lua-snmp6
"

log() {
  printf '%s\n' "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

if [ -z "$MONITORING_HOST" ]; then
  log "Usage: $0 <MONITORING_HOST_IP>"
  log "  Example: $0 192.168.0.100"
  exit 1
fi

if command -v apk >/dev/null 2>&1; then
  PKG_MANAGER="apk"
elif command -v opkg >/dev/null 2>&1; then
  PKG_MANAGER="opkg"
else
  die "neither apk nor opkg was found. This script supports OpenWrt 24.10/opkg and OpenWrt 25.12/apk."
fi

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

pkg_installed() {
  case "$PKG_MANAGER" in
    apk) apk info "$1" >/dev/null 2>&1 ;;
    opkg) opkg list-installed 2>/dev/null | grep -q "^$1 " ;;
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

is_legacy_cron_line() {
  case "$1" in
    *'/usr/bin/1-minute-script.sh'*|\
    *'/usr/bin/5-minute-script.sh'*|\
    *'/usr/bin/15-second-script.sh'*|\
    *'/usr/bin/device-status-ping.sh'*|\
    *'/usr/bin/new_device.sh'*|\
    *'/usr/bin/packet-loss.sh'*|\
    *'/usr/bin/openwrt-monitor-device-status.sh'*|\
    *'/usr/bin/openwrt-monitor-packet-loss.sh'*|\
    *'/usr/bin/openwrt-monitor-wan-info.sh'*|\
    *'/usr/bin/openwrt-monitor-service-health.sh'*|\
    *'/usr/bin/openwrt-monitor-wan-quality.sh'*|\
    *'/usr/bin/openwrt-monitor-filesystem.sh'*|\
    *'/usr/bin/openwrt-monitor-dhcp-pool.sh'*|\
    *'/usr/bin/openwrt-monitor-link-health.sh'*|\
    *'/usr/bin/openwrt-monitor-softnet.sh'*|\
    *'/usr/bin/openwrt-monitor-ipv6-health.sh'*|\
    *'/usr/bin/openwrt-monitor-inodes.sh'*|\
    *'/usr/bin/openwrt-monitor-firewall-counters.sh'*|\
    *'/usr/bin/openwrt-monitor-sqm.sh'*|\
    *'/usr/bin/openwrt-monitor-wifi-radio.sh'*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

filter_crontab() {
  while IFS= read -r line; do
    case "$line" in
      *'/usr/bin/openwrt-grafana-monitor-metrics'*) continue ;;
      *'/usr/bin/openwrt-grafana-monitor-sqm'*) continue ;;
    esac

    if is_legacy_cron_line "$line"; then
      if [ "$CLEANUP_LEGACY_CRON" = "1" ]; then
        log "    Removing legacy cron job: $line" >&2
        continue
      fi
      log "    WARNING: legacy monitor cron job still enabled: $line" >&2
    fi

    printf '%s\n' "$line"
  done
}

detect_legacy_cron_jobs() {
  found=0
  while IFS= read -r line; do
    if is_legacy_cron_line "$line"; then
      log "    $line"
      found=1
    fi
  done
  [ "$found" = "1" ]
}

resolve_legacy_cron_cleanup() {
  case "$CLEANUP_LEGACY_CRON" in
    1|yes|YES|true|TRUE)
      CLEANUP_LEGACY_CRON="1"
      return
      ;;
    0|no|NO|false|FALSE)
      CLEANUP_LEGACY_CRON="0"
      return
      ;;
    auto|"")
      ;;
    *)
      log "    WARNING: invalid CLEANUP_LEGACY_CRON='$CLEANUP_LEGACY_CRON'; using auto"
      ;;
  esac

  CLEANUP_LEGACY_CRON="0"
  if [ -t 0 ]; then
    printf 'Disable these known old monitoring cron jobs? [y/N] '
    read -r answer || answer=""
    case "$answer" in
      y|Y|yes|YES) CLEANUP_LEGACY_CRON="1" ;;
    esac
  else
    log "    Non-interactive shell detected; keeping legacy cron jobs."
    log "    Rerun with CLEANUP_LEGACY_CRON=1 to remove known old monitor jobs."
  fi
}

log "==> OpenWrt Grafana Monitor setup"
log "    Monitoring host: $MONITORING_HOST"
log "    Package manager: $PKG_MANAGER"
log "    Exporter interface: $EXPORTER_LISTEN_INTERFACE"
log ""

log "==> Updating package list..."
pkg_update

log "==> Installing required exporter packages..."
pkg_install_required $REQUIRED_PACKAGES

for package in $OPTIONAL_PACKAGES; do
  log "==> Installing optional package: $package"
  if ! pkg_install_optional "$package"; then
    log "    WARNING: optional package unavailable or failed to install: $package"
  fi
done

if pkg_installed mwan3; then
  log "==> mwan3 detected, installing mwan3 exporter..."
  if ! pkg_install_optional prometheus-node-exporter-lua-mwan3; then
    log "    WARNING: mwan3 exporter unavailable or failed to install"
  fi
fi

log "==> Configuring prometheus-node-exporter-lua..."
uci set prometheus-node-exporter-lua.main.listen_interface="$EXPORTER_LISTEN_INTERFACE"
uci set prometheus-node-exporter-lua.main.listen_port='9100'
uci commit prometheus-node-exporter-lua

log "==> Installing custom textfile metrics..."
mkdir -p /var/prometheus

cat >/etc/openwrt-grafana-monitor.conf <<EOF
PING_TARGET="$PING_TARGET"
DNS_PROBE_HOST="$DNS_PROBE_HOST"
DNS_PROBE_TIMEOUT="$DNS_PROBE_TIMEOUT"
PUBLIC_IP_LOOKUP="$PUBLIC_IP_LOOKUP"
PUBLIC_IP_URL="$PUBLIC_IP_URL"
PUBLIC_IP_CHECK_INTERVAL="$PUBLIC_IP_CHECK_INTERVAL"
ENABLE_SQM_METRICS="$ENABLE_SQM_METRICS"
SQM_INTERFACES="$SQM_INTERFACES"
EOF

cat >/usr/bin/openwrt-grafana-monitor-metrics <<'EOF'
#!/bin/sh
set -eu

CONF="/etc/openwrt-grafana-monitor.conf"
OUT_DIR="/var/prometheus"
OUT_FILE="$OUT_DIR/openwrt-grafana-monitor.prom"
TMP_FILE="$OUT_FILE.$$"

PING_TARGET="1.1.1.1"
DNS_PROBE_HOST="openwrt.org"
DNS_PROBE_TIMEOUT="5"
PUBLIC_IP_LOOKUP="0"
PUBLIC_IP_URL="https://api.ipify.org"
PUBLIC_IP_CHECK_INTERVAL="900"
PUBLIC_IP_CACHE_FILE="/tmp/openwrt-grafana-monitor-public-ip"
PUBLIC_IP_TS_FILE="/tmp/openwrt-grafana-monitor-public-ip-ts"
PUBLIC_IP_LAST_FILE="/tmp/openwrt-grafana-monitor-last-public-ip"

[ -r "$CONF" ] && . "$CONF"

mkdir -p "$OUT_DIR"

escape_label() {
  printf '%s' "${1:-}" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

hostname="$(uci get system.@system[0].hostname 2>/dev/null || echo openwrt)"
wan_iface="$(uci get network.wan.device 2>/dev/null || uci get network.wan.ifname 2>/dev/null || echo wan)"
wan_ip="$(ip -4 addr show dev "$wan_iface" 2>/dev/null | awk '/inet / { sub(/\/.*/, "", $2); print $2; exit }')"
public_ip=""
wan_public_ip_changed="0"

if [ "$PUBLIC_IP_LOOKUP" = "1" ]; then
  now="$(date +%s 2>/dev/null || echo 0)"
  last_check="0"
  [ -r "$PUBLIC_IP_TS_FILE" ] && last_check="$(cat "$PUBLIC_IP_TS_FILE" 2>/dev/null || echo 0)"
  [ -r "$PUBLIC_IP_CACHE_FILE" ] && public_ip="$(cat "$PUBLIC_IP_CACHE_FILE" 2>/dev/null || true)"

  case "$PUBLIC_IP_CHECK_INTERVAL" in *[!0-9]*|"") PUBLIC_IP_CHECK_INTERVAL="900" ;; esac
  case "$now" in *[!0-9]*|"") now="0" ;; esac
  case "$last_check" in *[!0-9]*|"") last_check="0" ;; esac

  should_check=1
  if [ "$now" != "0" ] && [ $((now - last_check)) -lt "$PUBLIC_IP_CHECK_INTERVAL" ]; then
    should_check=0
  fi

  if [ "$should_check" = "1" ]; then
    fetched_ip=""
    if command -v wget >/dev/null 2>&1; then
      fetched_ip="$(wget -qO- "$PUBLIC_IP_URL" 2>/dev/null | tr -d '\r\n' || true)"
    elif command -v curl >/dev/null 2>&1; then
      fetched_ip="$(curl -fsS "$PUBLIC_IP_URL" 2>/dev/null | tr -d '\r\n' || true)"
    fi

    if [ -n "$fetched_ip" ]; then
      last_ip=""
      [ -r "$PUBLIC_IP_LAST_FILE" ] && last_ip="$(cat "$PUBLIC_IP_LAST_FILE" 2>/dev/null || true)"
      if [ -n "$last_ip" ] && [ "$fetched_ip" != "$last_ip" ]; then
        wan_public_ip_changed="1"
      fi
      public_ip="$fetched_ip"
      printf '%s' "$public_ip" >"$PUBLIC_IP_CACHE_FILE"
      printf '%s' "$public_ip" >"$PUBLIC_IP_LAST_FILE"
    fi
    [ "$now" != "0" ] && printf '%s' "$now" >"$PUBLIC_IP_TS_FILE"
  fi
fi

{
  echo '# HELP dhcp_lease Active DHCP lease expiry timestamp from /tmp/dhcp.leases.'
  echo '# TYPE dhcp_lease gauge'
  if [ -r /tmp/dhcp.leases ]; then
    awk '
      function esc(v) { gsub(/\\/,"\\\\",v); gsub(/"/,"\\\"",v); return v }
      NF >= 4 {
        hostname = $4
        if (hostname == "*") hostname = ""
        printf "dhcp_lease{mac=\"%s\",ip=\"%s\",hostname=\"%s\"} %s\n", esc(toupper($2)), esc($3), esc(hostname), $1
      }
    ' /tmp/dhcp.leases
  fi

  echo '# HELP router_device_up Device online status derived from active DHCP leases and static DHCP host config.'
  echo '# TYPE router_device_up gauge'
  if [ -r /tmp/dhcp.leases ]; then
    awk '
      function esc(v) { gsub(/\\/,"\\\\",v); gsub(/"/,"\\\"",v); return v }
      NF >= 4 {
        hostname = $4
        if (hostname == "*") hostname = $3
        printf "router_device_up{device=\"%s\",status=\"online\",mac=\"%s\",ip=\"%s\"} 1\n", esc(hostname), esc(toupper($2)), esc($3)
      }
    ' /tmp/dhcp.leases
  fi

  uci show dhcp 2>/dev/null | awk -F= '
    BEGIN {
      while ((getline line < "/tmp/dhcp.leases") > 0) {
        split(line, fields, " ")
        active[toupper(fields[2])] = 1
      }
      close("/tmp/dhcp.leases")
    }
    /^dhcp\.[^.]+\.name=/ { gsub(/\047/, "", $2); section=$1; sub(/\.name$/, "", section); name[section]=$2 }
    /^dhcp\.[^.]+\.mac=/ { gsub(/\047/, "", $2); section=$1; sub(/\.mac$/, "", section); mac[section]=toupper($2) }
    /^dhcp\.[^.]+\.ip=/ { gsub(/\047/, "", $2); section=$1; sub(/\.ip$/, "", section); ip[section]=$2 }
    END {
      for (section in mac) {
        if (mac[section] in active) continue
        device=name[section]; if (device == "") device=ip[section]; if (device == "") device=mac[section]
        printf "router_device_up{device=\"%s\",status=\"offline\",mac=\"%s\",ip=\"%s\"} 0\n", device, mac[section], ip[section]
      }
    }
  '

  echo '# HELP wan_info WAN and optional public IP metadata.'
  echo '# TYPE wan_info gauge'
  printf 'wan_info{wanip="%s",publicip="%s",hostname="%s"} 1\n' \
    "$(escape_label "$wan_ip")" "$(escape_label "$public_ip")" "$(escape_label "$hostname")"

  echo '# HELP packet_loss Packet loss percentage to configured probe target.'
  echo '# TYPE packet_loss gauge'
  loss="0"
  if command -v ping >/dev/null 2>&1; then
    loss="$(ping -c 3 -W 2 "$PING_TARGET" 2>/dev/null | awk -F, '/packet loss/ { gsub(/[^0-9.]/, "", $3); print $3; found=1 } END { if (!found) print 100 }')"
  fi
  printf 'packet_loss{target="%s"} %s\n' "$(escape_label "$PING_TARGET")" "$loss"

  echo '# HELP dns_probe_success DNS resolution probe result for the configured host.'
  echo '# TYPE dns_probe_success gauge'
  echo '# HELP dns_probe_duration_seconds DNS resolution probe duration in whole seconds.'
  echo '# TYPE dns_probe_duration_seconds gauge'
  dns_success="0"
  dns_duration=""
  case "$DNS_PROBE_TIMEOUT" in *[!0-9]*|"") DNS_PROBE_TIMEOUT="5" ;; esac
  dns_start="$(date +%s 2>/dev/null || echo 0)"
  if command -v nslookup >/dev/null 2>&1; then
    if nslookup "$DNS_PROBE_HOST" >/dev/null 2>&1; then
      dns_success="1"
    fi
  elif command -v ping >/dev/null 2>&1; then
    if ping -c 1 -W "$DNS_PROBE_TIMEOUT" "$DNS_PROBE_HOST" >/dev/null 2>&1; then
      dns_success="1"
    fi
  fi
  dns_end="$(date +%s 2>/dev/null || echo 0)"
  case "$dns_start:$dns_end" in
    *[!0-9:]*|0:*) dns_duration="" ;;
    *) dns_duration="$((dns_end - dns_start))" ;;
  esac
  printf 'dns_probe_success{host="%s"} %s\n' "$(escape_label "$DNS_PROBE_HOST")" "$dns_success"
  [ -n "$dns_duration" ] && printf 'dns_probe_duration_seconds{host="%s"} %s\n' "$(escape_label "$DNS_PROBE_HOST")" "$dns_duration"

  echo '# HELP overlay_bytes_total Total overlay rootfs_data filesystem size in bytes.'
  echo '# TYPE overlay_bytes_total gauge'
  echo '# HELP overlay_bytes_used Used overlay rootfs_data filesystem bytes.'
  echo '# TYPE overlay_bytes_used gauge'
  overlay_line="$(df -P /overlay 2>/dev/null | awk 'NR==2')"
  if [ -n "$overlay_line" ]; then
    ov_total_kb="$(printf '%s\n' "$overlay_line" | awk '{print $2}')"
    ov_used_kb="$(printf '%s\n' "$overlay_line" | awk '{print $3}')"
    printf 'overlay_bytes_total %s\n' "$((ov_total_kb * 1024))"
    printf 'overlay_bytes_used %s\n' "$((ov_used_kb * 1024))"
  fi

  echo '# HELP gateway_packet_loss Packet loss percentage to the IPv4 default gateway.'
  echo '# TYPE gateway_packet_loss gauge'
  gateway="$(ip -4 route show default 2>/dev/null | awk '/default/ {print $3; exit}')"
  gateway_loss="100"
  if [ -n "$gateway" ] && command -v ping >/dev/null 2>&1; then
    gateway_loss="$(ping -c 3 -W 2 "$gateway" 2>/dev/null | awk -F, '/packet loss/ { gsub(/[^0-9.]/, "", $3); print $3; found=1 } END { if (!found) print 100 }')"
  fi
  printf 'gateway_packet_loss{gateway="%s"} %s\n' "$(escape_label "$gateway")" "$gateway_loss"

  echo '# HELP wan_public_ip_changed 1 if public IP changed since the last successful lookup, else 0.'
  echo '# TYPE wan_public_ip_changed gauge'
  printf 'wan_public_ip_changed %s\n' "$wan_public_ip_changed"

  echo '# HELP dhcpv6_lease_count Number of active DHCPv6/RA leases known to odhcpd.'
  echo '# TYPE dhcpv6_lease_count gauge'
  printf 'dhcpv6_lease_count %s\n' "$([ -r /tmp/hosts/odhcpd ] && wc -l < /tmp/hosts/odhcpd || echo 0)"
} >"$TMP_FILE"

mv "$TMP_FILE" "$OUT_FILE"
EOF

chmod 0755 /usr/bin/openwrt-grafana-monitor-metrics
/usr/bin/openwrt-grafana-monitor-metrics

if [ "$ENABLE_SQM_METRICS" = "1" ]; then
  if [ -z "$SQM_INTERFACES" ]; then
    log "    WARNING: ENABLE_SQM_METRICS=1 but SQM_INTERFACES is empty; SQM collector will emit no interface samples."
  fi

  cat >/usr/bin/openwrt-grafana-monitor-sqm <<'EOF'
#!/bin/sh
set -eu

CONF="/etc/openwrt-grafana-monitor.conf"
OUT_DIR="/var/prometheus"
OUT_FILE="$OUT_DIR/openwrt-grafana-monitor-sqm.prom"
TMP_FILE="$OUT_FILE.$$"

ENABLE_SQM_METRICS="0"
SQM_INTERFACES=""

[ -r "$CONF" ] && . "$CONF"

mkdir -p "$OUT_DIR"

escape_label() {
  printf '%s' "${1:-}" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

{
  echo '# HELP sqm_backlog_bytes SQM/cake qdisc backlog bytes by configured interface.'
  echo '# TYPE sqm_backlog_bytes gauge'
  echo '# HELP sqm_dropped_packets_total SQM/cake qdisc dropped packets by configured interface.'
  echo '# TYPE sqm_dropped_packets_total counter'
  echo '# HELP sqm_overlimits_total SQM/cake qdisc overlimits by configured interface.'
  echo '# TYPE sqm_overlimits_total counter'

  if [ "$ENABLE_SQM_METRICS" = "1" ] && [ -n "$SQM_INTERFACES" ] && command -v tc >/dev/null 2>&1; then
    for iface in $SQM_INTERFACES; do
      direction="egress"
      case "$iface" in ifb*) direction="ingress" ;; esac
      tc -s qdisc show dev "$iface" 2>/dev/null | awk \
        -v iface="$(escape_label "$iface")" \
        -v direction="$direction" '
        function bytes(v, n, u) {
          n = v
          u = v
          sub(/[kKmMgG]?[bB]$/, "", n)
          sub(/^[0-9.]+/, "", u)
          if (u == "Kb" || u == "KB" || u == "kb") return n * 1024
          if (u == "Mb" || u == "MB" || u == "mb") return n * 1024 * 1024
          if (u == "Gb" || u == "GB" || u == "gb") return n * 1024 * 1024 * 1024
          return n + 0
        }
        /backlog / {
          for (i = 1; i <= NF; i++) {
            if ($i == "backlog" && (i + 1) <= NF) backlog += bytes($(i + 1))
          }
        }
        /\(dropped / {
          for (i = 1; i <= NF; i++) {
            if ($i == "(dropped" && (i + 1) <= NF) { v = $(i + 1); gsub(/,/, "", v); dropped += v + 0 }
            if ($i == "overlimits" && (i + 1) <= NF) overlimits += $(i + 1) + 0
          }
        }
        END {
          printf "sqm_backlog_bytes{iface=\"%s\",direction=\"%s\"} %.0f\n", iface, direction, backlog + 0
          printf "sqm_dropped_packets_total{iface=\"%s\",direction=\"%s\"} %.0f\n", iface, direction, dropped + 0
          printf "sqm_overlimits_total{iface=\"%s\",direction=\"%s\"} %.0f\n", iface, direction, overlimits + 0
        }'
    done
  fi
} >"$TMP_FILE"

mv "$TMP_FILE" "$OUT_FILE"
EOF

  chmod 0755 /usr/bin/openwrt-grafana-monitor-sqm
  /usr/bin/openwrt-grafana-monitor-sqm
fi

if command -v crontab >/dev/null 2>&1; then
  tmp_cron="/tmp/openwrt-grafana-monitor.cron.$$"
  current_cron="/tmp/openwrt-grafana-monitor.current-cron.$$"
  crontab -l 2>/dev/null >"$current_cron" || true

  log "==> Checking for legacy monitor cron jobs..."
  if detect_legacy_cron_jobs <"$current_cron"; then
    resolve_legacy_cron_cleanup
  else
    log "    No known legacy monitor cron jobs detected."
  fi

  filter_crontab <"$current_cron" >"$tmp_cron"
  echo '* * * * * /usr/bin/openwrt-grafana-monitor-metrics >/dev/null 2>&1' >>"$tmp_cron"
  if [ "$ENABLE_SQM_METRICS" = "1" ]; then
    echo '* * * * * /usr/bin/openwrt-grafana-monitor-sqm >/dev/null 2>&1' >>"$tmp_cron"
  fi
  crontab "$tmp_cron"
  rm -f "$tmp_cron" "$current_cron"
  /etc/init.d/cron enable >/dev/null 2>&1 || true
  /etc/init.d/cron restart >/dev/null 2>&1 || true
fi

log "==> Enabling and starting prometheus-node-exporter-lua..."
/etc/init.d/prometheus-node-exporter-lua enable
/etc/init.d/prometheus-node-exporter-lua restart

sleep 2

LAN_IP="$(uci get network.lan.ipaddr 2>/dev/null || echo '<ROUTER_IP>')"
METRICS_URL="http://$LAN_IP:9100/metrics"

if fetch_url "$METRICS_URL" >/dev/null 2>&1; then
  log "    OK: metrics endpoint is up at $METRICS_URL"
else
  log "    WARNING: local metrics endpoint is not responding yet."
  log "    Check with: wget -qO- $METRICS_URL | head"
fi

log "==> Configuring remote syslog to $MONITORING_HOST:$SYSLOG_PORT ..."
uci set system.@system[0].log_ip="$MONITORING_HOST"
uci set system.@system[0].log_port="$SYSLOG_PORT"
uci set system.@system[0].log_proto='udp'
uci set system.@system[0].log_hostname="$(uci get system.@system[0].hostname 2>/dev/null || echo openwrt)"
uci commit system

/etc/init.d/log restart
log "    OK: syslog configured"

log ""
log "==> Setup complete"
log ""
log "    Metrics:  http://$LAN_IP:9100/metrics"
log "    Syslog:   $MONITORING_HOST:$SYSLOG_PORT/udp"
log "    Textfile: /var/prometheus/openwrt-grafana-monitor.prom"
log ""
log "    Now start the Docker stack on $MONITORING_HOST:"
log "    docker compose up -d"
log ""
