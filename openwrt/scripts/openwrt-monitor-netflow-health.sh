#!/bin/sh

# NetFlow exporter health for the `netflow` profile.
#
# The flow records themselves go straight to the Akvorado inlet over UDP and
# never touch Prometheus. What this exports is whether that pipeline is
# actually working, and the two ways it fails quietly:
#
#   - libpcap ring overflow (openwrt_netflow_pcap_packets_dropped_total): the
#     router could not keep up, so flows are missing packets.
#   - flow-table overflow (openwrt_netflow_flows_force_expired_total):
#     max_flows was hit and flows were expired early, truncating byte counts.
#
# Neither produces an error anywhere; both make the data quietly wrong. They
# are exported as counters so the dashboard can show them increasing.
#
# openwrt_netflow_ifindex is also exported here. Akvorado resolves interfaces
# by ifIndex and DISCARDS any flow whose ifIndex it does not know, so having
# the real value visible in Grafana turns "no flows at all" from a guessing
# game into a one-panel comparison against akvorado/exporters.yaml.
set -e
set +u

CONF="/etc/openwrt-grafana-monitor.conf"
[ -r "$CONF" ] && . "$CONF"

OUTDIR="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
OUTFILE="$OUTDIR/openwrt_netflow.prom"
TMPFILE="/tmp/.openwrt-monitor-openwrt_netflow.$$"
STATSFILE="/tmp/.openwrt-monitor-netflow-stats.$$"
SOFTFLOWCTL_BIN="${OPENWRT_MONITOR_SOFTFLOWCTL_BIN:-softflowctl}"
SOFTFLOWD_RUN_DIR="${OPENWRT_MONITOR_SOFTFLOWD_RUN_DIR:-/var/run}"
SYSFS_NET="${OPENWRT_MONITOR_SYSFS_NET:-/sys/class/net}"
NETFLOW_INTERFACES="${NETFLOW_INTERFACES:-}"

mkdir -p "$OUTDIR"
trap 'rm -f "$TMPFILE" "$STATSFILE"' EXIT

headers() {
  printf '# HELP openwrt_netflow_collector_available Whether NetFlow exporter health was collected successfully.\n'
  printf '# TYPE openwrt_netflow_collector_available gauge\n'
  printf '# HELP openwrt_netflow_exporter_up Whether a softflowd instance is running for this capture interface.\n'
  printf '# TYPE openwrt_netflow_exporter_up gauge\n'
  printf '# HELP openwrt_netflow_ifindex Kernel ifIndex of the capture interface, as reported in exported flows.\n'
  printf '# TYPE openwrt_netflow_ifindex gauge\n'
  printf '# HELP openwrt_netflow_active_flows Flows currently tracked in the softflowd flow table.\n'
  printf '# TYPE openwrt_netflow_active_flows gauge\n'
  printf '# HELP openwrt_netflow_packets_processed_total Packets processed by softflowd.\n'
  printf '# TYPE openwrt_netflow_packets_processed_total counter\n'
  printf '# HELP openwrt_netflow_flows_expired_total Flows expired from the softflowd flow table.\n'
  printf '# TYPE openwrt_netflow_flows_expired_total counter\n'
  printf '# HELP openwrt_netflow_flows_force_expired_total Flows expired early because the flow table was full. Non-zero means byte counts are truncated; raise NETFLOW_MAX_FLOWS.\n'
  printf '# TYPE openwrt_netflow_flows_force_expired_total counter\n'
  printf '# HELP openwrt_netflow_flows_exported_total Flows exported to the collector.\n'
  printf '# TYPE openwrt_netflow_flows_exported_total counter\n'
  printf '# HELP openwrt_netflow_export_failures_total Flows that could not be sent to the collector.\n'
  printf '# TYPE openwrt_netflow_export_failures_total counter\n'
  printf '# HELP openwrt_netflow_pcap_packets_received_total Packets handed to softflowd by libpcap.\n'
  printf '# TYPE openwrt_netflow_pcap_packets_received_total counter\n'
  printf '# HELP openwrt_netflow_pcap_packets_dropped_total Packets dropped by libpcap because softflowd could not keep up. Non-zero means flows are incomplete.\n'
  printf '# TYPE openwrt_netflow_pcap_packets_dropped_total counter\n'
}

# Exposition must stay parseable even when softflowd is missing entirely, and
# an absent exporter must read as an explicit 0 rather than as an absent
# series that a dashboard would render as "no data" alongside a healthy router.
fail_closed() {
  {
    headers
    printf 'openwrt_netflow_collector_available 0\n'
  } > "$TMPFILE"
  mv "$TMPFILE" "$OUTFILE"
  exit 0
}

# NETFLOW_INTERFACES comes from /etc/openwrt-grafana-monitor.conf, written by
# setup.sh. Without it there is nothing to report on -- which is the normal
# state on a router where the netflow profile was never enabled.
[ -n "$NETFLOW_INTERFACES" ] || fail_closed

emit_ifindex() {
  iface="$1"
  if [ -r "$SYSFS_NET/$iface/ifindex" ]; then
    read -r ifindex < "$SYSFS_NET/$iface/ifindex" || return 0
    case "$ifindex" in
      ''|*[!0-9]*) return 0 ;;
    esac
    printf 'openwrt_netflow_ifindex{interface="%s"} %s\n' "$iface" "$ifindex"
  fi
}

# softflowctl talks to the running process over its control socket, so a
# successful reply is itself the liveness check -- a stale pid file cannot
# fake it.
emit_stats() {
  iface="$1"
  socket="$SOFTFLOWD_RUN_DIR/softflowd-$iface.ctl"

  command -v "$SOFTFLOWCTL_BIN" >/dev/null 2>&1 || return 1
  [ -e "$socket" ] || return 1
  "$SOFTFLOWCTL_BIN" -c "$socket" statistics > "$STATSFILE" 2>/dev/null || return 1
  [ -s "$STATSFILE" ] || return 1

  awk -v iface="$iface" '
    function emit(name, value) {
      printf "openwrt_netflow_%s{interface=\"%s\"} %s\n", name, iface, value
    }
    /^Number of active flows:/        { emit("active_flows", $5) }
    /^Packets processed:/             { emit("packets_processed_total", $3) }
    /^Flows expired:/ {
      # "Flows expired: N (M forced)" -- M is the flow-table overflow counter.
      forced = $4; gsub(/[()]/, "", forced)
      emit("flows_expired_total", $3)
      emit("flows_force_expired_total", forced)
    }
    /^Flows exported:/ {
      # "Flows exported: N (R records) in P packets (F failures)"
      failures = $(NF - 1); gsub(/[()]/, "", failures)
      emit("flows_exported_total", $3)
      emit("export_failures_total", failures)
    }
    /^Packets received by libpcap:/   { emit("pcap_packets_received_total", $5) }
    /^Packets dropped by libpcap:/    { emit("pcap_packets_dropped_total", $5) }
  ' "$STATSFILE"
}

{
  headers

  any_up=0
  for netflow_iface in $NETFLOW_INTERFACES; do
    emit_ifindex "$netflow_iface"
    if emit_stats "$netflow_iface"; then
      printf 'openwrt_netflow_exporter_up{interface="%s"} 1\n' "$netflow_iface"
      any_up=1
    else
      printf 'openwrt_netflow_exporter_up{interface="%s"} 0\n' "$netflow_iface"
    fi
  done

  # Available means "this collector ran and reached at least one exporter".
  # Every interface being down is a real, reportable state, not a collector
  # failure -- but it must not read as healthy either.
  printf 'openwrt_netflow_collector_available %s\n' "$any_up"
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
