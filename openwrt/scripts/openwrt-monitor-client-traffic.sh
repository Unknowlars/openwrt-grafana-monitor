#!/bin/sh

# libubox's jshn.sh references unset internal variables while it initialises.
# setup.sh itself uses `set -u`; make the helper resilient even if its shell
# option state is inherited, otherwise jshn's JSON_PREFIX expansion aborts the
# whole setup rather than producing the intended fail-closed availability 0.
set -e
set +u

# nlbwmon's counters are cumulative for its current accounting period (one
# month by default). The rollover is a normal Prometheus counter reset, so
# rate() and increase() handle it without collector-side adjustment.
OUTDIR="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
OUTFILE="$OUTDIR/openwrt_client_traffic.prom"
TMPFILE="/tmp/.openwrt-monitor-openwrt_client_traffic.$$"
RAWFILE="/tmp/.openwrt-monitor-nlbw.$$.json"
ROWSFILE="/tmp/.openwrt-monitor-nlbw.$$.rows"
JSHN_PATH="${OPENWRT_MONITOR_JSHN_PATH:-/usr/share/libubox/jshn.sh}"
NLBW_BIN="${OPENWRT_MONITOR_NLBW_BIN:-nlbw}"

mkdir -p "$OUTDIR"
rm -f "$OUTFILE".[0-9]*
trap 'rm -f "$TMPFILE" "$RAWFILE" "$ROWSFILE"' EXIT

# Created unconditionally: the record loop below is the only writer, so a
# legitimately empty nlbwmon result set (fresh install, accounting-period
# rollover, just after `nlbw -c commit`) would otherwise leave the awk at the
# bottom with no input file, aborting under `set -e` before the mv and leaving
# the *previous* period's .prom in place still reporting available 1. An empty
# period is not a collector failure, so the honest output is
# `..._collector_available 1` with no per-client series.
: > "$ROWSFILE"

write_headers() {
  printf '# HELP openwrt_client_traffic_collector_available Whether nlbwmon client traffic accounting was collected successfully.\n'
  printf '# TYPE openwrt_client_traffic_collector_available gauge\n'
  printf '# HELP openwrt_client_bytes_total Per-client traffic bytes in the current nlbwmon accounting period.\n'
  printf '# TYPE openwrt_client_bytes_total counter\n'
  printf '# HELP openwrt_client_packets_total Per-client traffic packets in the current nlbwmon accounting period.\n'
  printf '# TYPE openwrt_client_packets_total counter\n'
  printf '# HELP openwrt_client_connections_total Per-client connections in the current nlbwmon accounting period.\n'
  printf '# TYPE openwrt_client_connections_total counter\n'
}

fail_closed() {
  {
    write_headers
    printf 'openwrt_client_traffic_collector_available 0\n'
  } > "$TMPFILE"
  mv "$TMPFILE" "$OUTFILE"
  exit 0
}

valid_mac() {
  case "$1" in
    [0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]) return 0 ;;
    *) return 1 ;;
  esac
}

valid_number() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

service_bucket() {
  case "$1" in
    HTTPS|https) printf '%s\n' https ;;
    HTTP|http) printf '%s\n' http ;;
    DNS|dns) printf '%s\n' dns ;;
    QUIC|quic) printf '%s\n' quic ;;
    SSH|ssh) printf '%s\n' ssh ;;
    SMB|smb) printf '%s\n' smb ;;
    NTP|ntp) printf '%s\n' ntp ;;
    IMAPS|imaps) printf '%s\n' imaps ;;
    RTP|rtp) printf '%s\n' rtp ;;
    null|'') printf '%s\n' other ;;
    # Any protocol name outside the trimmed set (nlbwmon's built-in
    # classifications aren't fully removed by trimming its protocol file, and
    # this list is intentionally a small subset of the ~45 shipped buckets --
    # buckets to "other" rather than failing the record. A
    # single unrecognized bucket must never zero out an entire router's
    # traffic accounting.
    *) printf '%s\n' other ;;
  esac
}

if ! command -v "$NLBW_BIN" >/dev/null 2>&1 || [ ! -r "$JSHN_PATH" ]; then
  fail_closed
fi

if ! "$NLBW_BIN" -c json > "$RAWFILE" 2>/dev/null; then
  fail_closed
fi

# jshn is supplied by libubox, an nlbwmon dependency on OpenWrt. Validate the
# observed schema before reading positional records so a package format change
# becomes an explicit unavailable state, never mislabelled traffic.
. "$JSHN_PATH"
json_init
if ! json_load "$(cat "$RAWFILE")"; then
  fail_closed
fi
if ! json_select columns; then
  fail_closed
fi
expected_columns='family proto port mac ip conns rx_bytes rx_pkts tx_bytes tx_pkts layer7'
actual_columns=''
for column_index in 1 2 3 4 5 6 7 8 9 10 11; do
  json_get_var column "$column_index"
  actual_columns="$actual_columns${actual_columns:+ }$column"
done
json_select ..
[ "$actual_columns" = "$expected_columns" ] || fail_closed

if ! json_select data; then
  fail_closed
fi
json_get_keys records
for record in $records; do
  json_select "$record" || fail_closed
  json_get_var mac 4
  json_get_var conns 6
  json_get_var rx_bytes 7
  json_get_var rx_pkts 8
  json_get_var tx_bytes 9
  json_get_var tx_pkts 10
  json_get_var layer7 11
  json_select ..

  mac=$(printf '%s' "$mac" | tr 'A-F' 'a-f')
  valid_mac "$mac" || fail_closed
  valid_number "$conns" && valid_number "$rx_bytes" && valid_number "$rx_pkts" && \
    valid_number "$tx_bytes" && valid_number "$tx_pkts" || fail_closed
  service=$(service_bucket "$layer7") || fail_closed
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$mac" "$service" "$conns" "$rx_bytes" "$rx_pkts" "$tx_bytes" "$tx_pkts" >> "$ROWSFILE"
done
json_select ..

{
  write_headers
  # nlbwmon can have IPv4 and IPv6 records for the same MAC/service. Aggregate
  # before exposition so every Prometheus label set occurs exactly once.
  awk -F '\t' '
    {
      key = $1 SUBSEP $2
      conns[key] += $3
      rx_bytes[key] += $4
      rx_pkts[key] += $5
      tx_bytes[key] += $6
      tx_pkts[key] += $7
    }
    END {
      for (key in conns) {
        split(key, parts, SUBSEP)
        mac = parts[1]
        service = parts[2]
        printf "openwrt_client_bytes_total{mac=\"%s\",direction=\"in\",service=\"%s\"} %.0f\n", mac, service, rx_bytes[key]
        printf "openwrt_client_bytes_total{mac=\"%s\",direction=\"out\",service=\"%s\"} %.0f\n", mac, service, tx_bytes[key]
        printf "openwrt_client_packets_total{mac=\"%s\",direction=\"in\",service=\"%s\"} %.0f\n", mac, service, rx_pkts[key]
        printf "openwrt_client_packets_total{mac=\"%s\",direction=\"out\",service=\"%s\"} %.0f\n", mac, service, tx_pkts[key]
        printf "openwrt_client_connections_total{mac=\"%s\",service=\"%s\"} %.0f\n", mac, service, conns[key]
      }
    }
  ' "$ROWSFILE"
  printf 'openwrt_client_traffic_collector_available 1\n'
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
