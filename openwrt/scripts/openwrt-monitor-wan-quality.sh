#!/bin/sh

set -e

# Overridable so the collector logic can be exercised in tests.
OUTDIR="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
OUTFILE="$OUTDIR/openwrt_wan_quality.prom"
# Stage outside $OUTDIR (same filesystem on OpenWrt: /var -> /tmp) and mv
# atomically into place, matching every sibling helper.
#
# This script had the longest staging window of any of them: the header block is
# written before up to three serial `ping -c 5` runs, so a partial
# openwrt_wan_quality.prom.<pid> sat in /var/prometheus for 15+ seconds out of
# every 5 minutes. That partial file is NOT scraped -- the textfile collector
# globs *.prom only, and 144 one-second polls spanning a confirmed collector run
# showed zero duplicated series. So the harm is not double-exposed metrics; it is
# that a crash, reboot, or killall mid-run leaves the partial file in a tmpfs
# directory forever, with no trap and no sweep to reclaim it.
TMPFILE="/tmp/.openwrt-monitor-openwrt_wan_quality.$$"
CONF="/etc/openwrt-grafana-monitor.conf"
COUNT="${1:-5}"
TIMEOUT="${2:-1}"
WAN_PROBE_TARGET="${WAN_PROBE_TARGET:-1.1.1.1}"
DNS_PROBE_HOST="${DNS_PROBE_HOST:-openwrt.org}"
DNS_PROBE_TIMEOUT="${DNS_PROBE_TIMEOUT:-5}"

[ -r "$CONF" ] && . "$CONF"

mkdir -p "$OUTDIR"
# Sweeps duplicate .prom.<pid> files left in $OUTDIR by the pre-fix staging bug
# above, so routers already running the old code self-heal on the next run.
# Removable once no deployed router predates this fix.
rm -f "$OUTFILE".[0-9]*
trap 'rm -f "$TMPFILE"' EXIT

escape_label() {
  printf '%s' "${1:-}" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

get_default_gateway() {
  ip route | awk '/^default/ {print $3; exit}'
}

get_upstream_dns() {
  for path in /tmp/resolv.conf.d/resolv.conf.auto /tmp/resolv.conf.auto /tmp/resolv.conf; do
    [ -f "$path" ] || continue
    awk '/^nameserver[[:space:]]+/ {print $2; exit}' "$path"
    return 0
  done

  return 1
}

write_probe() {
  target="$1"
  address="$2"

  [ -n "$address" ] || return

  stats=$(ping -c "$COUNT" -W "$TIMEOUT" "$address" 2>/dev/null | awk '
    /time=/ {
      split($0, parts, "time=")
      if (length(parts) > 1) {
        split(parts[2], rest, " ")
        t = rest[1] + 0
        count += 1
        sum += t
        if (count == 1 || t < min) {
          min = t
        }
        if (count == 1 || t > max) {
          max = t
        }
      }
    }
    /packet loss/ {
      split($0, parts, ",")
      if (length(parts) >= 3) {
        loss = parts[3]
        gsub(/[^0-9.]/, "", loss)
        loss += 0
      }
    }
    END {
      if (count > 0) {
        avg = sum / count
        jitter = max - min
        if (loss == "") {
          loss = 0
        }
        printf "%.3f %.3f %.3f 1\n", avg, jitter, loss
      } else {
        if (loss == "") {
          loss = 100
        }
        printf "0 0 %.3f 0\n", loss
      }
    }
  ')

  set -- $stats
  latency="$1"
  jitter="$2"
  loss="$3"
  success="$4"

  printf 'openwrt_wan_probe_latency_milliseconds{target="%s",address="%s"} %s\n' "$target" "$address" "$latency" >> "$TMPFILE"
  printf 'openwrt_wan_probe_jitter_milliseconds{target="%s",address="%s"} %s\n' "$target" "$address" "$jitter" >> "$TMPFILE"
  printf 'openwrt_wan_probe_packet_loss_percent{target="%s",address="%s"} %s\n' "$target" "$address" "$loss" >> "$TMPFILE"
  printf 'openwrt_wan_probe_success{target="%s",address="%s"} %s\n' "$target" "$address" "$success" >> "$TMPFILE"

  if [ "$target" = "gateway" ]; then
    printf 'gateway_packet_loss{gateway="%s"} %s\n' "$(escape_label "$address")" "$loss" >> "$TMPFILE"
  fi
}

write_dns_probe() {
  dns_success="0"
  dns_duration=""

  case "$DNS_PROBE_TIMEOUT" in
    ''|*[!0-9]*) DNS_PROBE_TIMEOUT="5" ;;
  esac

  dns_start="$(date +%s 2>/dev/null || printf '0')"
  if command -v nslookup >/dev/null 2>&1; then
    if nslookup "$DNS_PROBE_HOST" >/dev/null 2>&1; then
      dns_success="1"
    fi
  elif command -v ping >/dev/null 2>&1; then
    if ping -c 1 -W "$DNS_PROBE_TIMEOUT" "$DNS_PROBE_HOST" >/dev/null 2>&1; then
      dns_success="1"
    fi
  fi
  dns_end="$(date +%s 2>/dev/null || printf '0')"

  case "$dns_start:$dns_end" in
    *[!0-9:]*|0:*) dns_duration="" ;;
    *) dns_duration="$((dns_end - dns_start))" ;;
  esac

  printf 'dns_probe_success{host="%s"} %s\n' "$(escape_label "$DNS_PROBE_HOST")" "$dns_success" >> "$TMPFILE"
  [ -n "$dns_duration" ] && printf 'dns_probe_duration_seconds{host="%s"} %s\n' "$(escape_label "$DNS_PROBE_HOST")" "$dns_duration" >> "$TMPFILE"
}

gateway="$(get_default_gateway 2>/dev/null || true)"
resolver="$(get_upstream_dns 2>/dev/null || true)"

{
  printf '# HELP openwrt_wan_probe_latency_milliseconds Average ping latency in milliseconds.\n'
  printf '# TYPE openwrt_wan_probe_latency_milliseconds gauge\n'
  printf '# HELP openwrt_wan_probe_jitter_milliseconds Ping jitter estimated as max minus min latency in milliseconds.\n'
  printf '# TYPE openwrt_wan_probe_jitter_milliseconds gauge\n'
  printf '# HELP openwrt_wan_probe_packet_loss_percent Ping packet loss percentage.\n'
  printf '# TYPE openwrt_wan_probe_packet_loss_percent gauge\n'
  printf '# HELP openwrt_wan_probe_success Whether the probe succeeded.\n'
  printf '# TYPE openwrt_wan_probe_success gauge\n'
  printf '# HELP gateway_packet_loss Packet loss percentage to the IPv4 default gateway. Compatibility alias for older dashboards.\n'
  printf '# TYPE gateway_packet_loss gauge\n'
  printf '# HELP dns_probe_success DNS resolution probe result for the configured host. Compatibility alias for older dashboards.\n'
  printf '# TYPE dns_probe_success gauge\n'
  printf '# HELP dns_probe_duration_seconds DNS resolution probe duration in whole seconds. Compatibility alias for older dashboards.\n'
  printf '# TYPE dns_probe_duration_seconds gauge\n'
} > "$TMPFILE"

write_probe gateway "$gateway"

if [ -n "$resolver" ] && [ "$resolver" != "$gateway" ]; then
  write_probe resolver "$resolver"
fi

write_probe internet "$WAN_PROBE_TARGET"
write_dns_probe

mv "$TMPFILE" "$OUTFILE"
