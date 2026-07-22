#!/bin/sh

# Per-client conntrack occupancy and bounded WiFi association events. The
# client identity is read from the same getHostHints source as
# client_inventory.lua, then conntrack rows are counted once for every local
# client IP they contain. Raw hostapd messages remain in syslog/Loki; this
# helper exports only the bounded aggregate {ap,ssid,event} counter.
set -e
set +u

OUTDIR="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
OUTFILE="$OUTDIR/openwrt_client_conntrack.prom"
TMPFILE="/tmp/.openwrt-monitor-openwrt_client_conntrack.$$"
HOSTSFILE="/tmp/.openwrt-monitor-client-hosts.$$"
CONNTRACKFILE="/tmp/.openwrt-monitor-conntrack.$$"
EVENTSFILE="${OPENWRT_MONITOR_ASSOC_EVENTS_STATE:-/etc/openwrt-wifi-assoc-events}"
EVENTSTMP="${EVENTSFILE}.tmp.$$"
JSHN_PATH="${OPENWRT_MONITOR_JSHN_PATH:-/usr/share/libubox/jshn.sh}"
UBUS_BIN="${OPENWRT_MONITOR_UBUS_BIN:-ubus}"
CONNTRACK_BIN="${OPENWRT_MONITOR_CONNTRACK_BIN:-conntrack}"
LOGREAD_BIN="${OPENWRT_MONITOR_LOGREAD_BIN:-logread}"
AP_NAME=$(cat /proc/sys/kernel/hostname 2>/dev/null | tr -c 'A-Za-z0-9._-' '_' | sed 's/_$//')
AP_NAME="${AP_NAME:-unknown}"

mkdir -p "$OUTDIR"
rm -f "$OUTFILE".[0-9]*
trap 'rm -f "$TMPFILE" "$HOSTSFILE" "$HOSTSFILE.ifaces" "$HOSTSFILE.events" "$CONNTRACKFILE" "$EVENTSTMP"' EXIT

headers() {
  printf '# HELP openwrt_client_conntrack_collector_available Whether per-client conntrack entries were collected successfully.\n'
  printf '# TYPE openwrt_client_conntrack_collector_available gauge\n'
  printf '# HELP openwrt_client_conntrack_entries Current conntrack entries containing a known client IP.\n'
  printf '# TYPE openwrt_client_conntrack_entries gauge\n'
  printf '# HELP openwrt_wifi_assoc_events_collector_available Whether bounded WiFi association events were collected successfully.\n'
  printf '# TYPE openwrt_wifi_assoc_events_collector_available gauge\n'
  printf '# HELP openwrt_wifi_assoc_events_total Cumulative WiFi association events by AP, SSID, and event type.\n'
  printf '# TYPE openwrt_wifi_assoc_events_total counter\n'
}

fail_closed() {
  {
    headers
    printf 'openwrt_client_conntrack_collector_available 0\n'
    printf 'openwrt_wifi_assoc_events_collector_available 0\n'
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

valid_ipv4() {
  case "$1" in
    *[!0-9.]*|*..*|.*|*.) return 1 ;;
  esac
  old_ifs=$IFS
  IFS=.
  set -- $1
  IFS=$old_ifs
  [ "$#" = 4 ] || return 1
  for octet in "$@"; do
    case "$octet" in ''|*[!0-9]*) return 1 ;; esac
    [ "$octet" -le 255 ] 2>/dev/null || return 1
  done
}

command -v "$UBUS_BIN" >/dev/null 2>&1 || fail_closed
command -v "$CONNTRACK_BIN" >/dev/null 2>&1 || fail_closed
[ -r "$JSHN_PATH" ] || fail_closed

# jshn handles MAC-keyed objects safely; parsing getHostHints with sed would
# risk assigning a conntrack row to the wrong client after a format change.
. "$JSHN_PATH"
json_init
HOSTS_JSON=$($UBUS_BIN call luci-rpc getHostHints 2>/dev/null) || fail_closed
json_load "$HOSTS_JSON" || fail_closed
json_get_keys host_macs
for raw_mac in $host_macs; do
  mac=$(printf '%s' "$raw_mac" | tr 'A-F' 'a-f')
  valid_mac "$mac" || continue
  json_select "$raw_mac" || fail_closed
  if json_select ipaddrs; then
    json_get_var ip 1
    json_select ..
    if valid_ipv4 "$ip"; then printf '%s\t%s\n' "$mac" "$ip" >> "$HOSTSFILE"; fi
  fi
  json_select ..
done
[ -s "$HOSTSFILE" ] || fail_closed

"$CONNTRACK_BIN" -L > "$CONNTRACKFILE" 2>/dev/null || fail_closed

emit_conntrack() {
  awk -v ap="$AP_NAME" -F '\t' '
    NR == FNR {
      mac[$1] = 1
      if (!($2 in ipmac)) ipmac[$2] = $1
      else if (ipmac[$2] != $1) ambiguous[$2] = 1
      next
    }
    {
      delete touched
      fields = split($0, field, " ")
      for (i = 1; i <= fields; i++) {
        split(field[i], pair, "=")
        ip = pair[2]
        if (ip in ipmac && !(ip in ambiguous)) touched[ipmac[ip]] = 1
      }
      for (client in touched) count[client]++
    }
    END {
      for (client in mac) printf "%s\t%d\n", client, count[client] + 0
    }
  ' "$HOSTSFILE" "$CONNTRACKFILE" | sort -k1,1 | while IFS='	' read -r mac count; do
    printf 'openwrt_client_conntrack_entries{mac="%s"} %s\n' "$mac" "$count"
  done
}

# Persist only 12-ish aggregate counters and a bounded fingerprint cache for
# the router's finite log ring. This prevents each minute's logread snapshot
# from re-counting the same hostapd line while preserving counter semantics.
emit_assoc_events() {
  command -v "$LOGREAD_BIN" >/dev/null 2>&1 || return 1
  WIFI_JSON=$($UBUS_BIN call network.wireless status 2>/dev/null) || return 1
  json_cleanup >/dev/null 2>&1 || true
  json_load "$WIFI_JSON" || return 1
  : > "$HOSTSFILE.ifaces"
  json_get_keys radios
  for radio in $radios; do
    json_select "$radio" || return 1
    if json_select interfaces; then
      json_get_keys iface_indexes
      for iface_index in $iface_indexes; do
        json_select "$iface_index" || return 1
        json_get_var ifname ifname
        if json_select config; then json_get_var ssid ssid; json_select ..; fi
        json_select ..
        ssid=$(printf '%s' "${ssid:-}" | tr -c 'A-Za-z0-9._-' '_')
        [ -n "${ifname:-}" ] && [ -n "$ssid" ] && printf '%s\t%s\n' "$ifname" "$ssid" >> "$HOSTSFILE.ifaces"
      done
      json_select ..
    fi
    json_select ..
  done
  [ -s "$HOSTSFILE.ifaces" ] || return 1

  : > "$HOSTSFILE.events"
  "$LOGREAD_BIN" 2>/dev/null | awk '
    /AP-STA-CONNECTED|AP-STA-DISCONNECTED/ {
      event = ($0 ~ /AP-STA-CONNECTED/) ? "connected" : "disconnected"
      for (i = 1; i <= NF; i++) if ($i ~ /^AP-STA-(CONNECTED|DISCONNECTED)$/ && i > 1) {
        ifname = $(i - 1); sub(/:$/, "", ifname)
        print ifname "\t" event "\t" $0
      }
    }
  ' > "$HOSTSFILE.events"

  touch "$EVENTSFILE" || return 1
  awk -v ap="$AP_NAME" -F '\t' '
    FILENAME == ARGV[1] { ssid[$1] = $2; next }
    FILENAME == ARGV[2] {
      if ($1 == "counter") counter[$2 SUBSEP $3 SUBSEP $4] = $5
      else if ($1 == "seen") { seen[$2] = ++sequence; record[sequence] = $2 }
      next
    }
    {
      if (!($1 in ssid)) next
      fingerprint = $3
      if (!(fingerprint in seen)) counter[ap SUBSEP ssid[$1] SUBSEP $2]++
      seen[fingerprint] = ++sequence; record[sequence] = fingerprint
    }
    END {
      for (key in counter) { split(key, p, SUBSEP); print "counter\t" p[1] "\t" p[2] "\t" p[3] "\t" counter[key] }
      first = sequence > 512 ? sequence - 511 : 1
      for (i = first; i <= sequence; i++) if (seen[record[i]] == i) print "seen\t" record[i]
    }
  ' "$HOSTSFILE.ifaces" "$EVENTSFILE" "$HOSTSFILE.events" > "$EVENTSTMP"
  mv "$EVENTSTMP" "$EVENTSFILE"
  awk -F '\t' '$1 == "counter" { printf "openwrt_wifi_assoc_events_total{ap=\"%s\",ssid=\"%s\",event=\"%s\"} %s\n", $2, $3, $4, $5 }' "$EVENTSFILE"
}

{
  headers
  emit_conntrack
  printf 'openwrt_client_conntrack_collector_available 1\n'
  if emit_assoc_events; then
    printf 'openwrt_wifi_assoc_events_collector_available 1\n'
  else
    printf 'openwrt_wifi_assoc_events_collector_available 0\n'
  fi
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
