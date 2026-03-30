#!/bin/sh

set -e

OUTDIR="/var/prometheus"
OUTFILE="$OUTDIR/openwrt_wan_quality.prom"
TMPFILE="$OUTFILE.$$"
COUNT="${1:-5}"
TIMEOUT="${2:-1}"

mkdir -p "$OUTDIR"

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
} > "$TMPFILE"

write_probe gateway "$gateway"

if [ -n "$resolver" ] && [ "$resolver" != "$gateway" ]; then
  write_probe resolver "$resolver"
fi

write_probe internet 1.1.1.1

mv "$TMPFILE" "$OUTFILE"
