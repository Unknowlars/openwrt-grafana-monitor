#!/bin/sh

set -e

OUTDIR="/var/prometheus"
OUTFILE="$OUTDIR/openwrt_wifi_radio.prom"
TMPFILE="$OUTFILE.$$"

mkdir -p "$OUTDIR"

{
  printf '# HELP openwrt_wifi_radio_collector_available Whether iwinfo is available for WiFi radio collection.\n'
  printf '# TYPE openwrt_wifi_radio_collector_available gauge\n'
  printf '# HELP openwrt_wifi_channel WiFi channel per AP interface.\n'
  printf '# TYPE openwrt_wifi_channel gauge\n'
  printf '# HELP openwrt_wifi_frequency_hz WiFi center frequency in Hz per AP interface.\n'
  printf '# TYPE openwrt_wifi_frequency_hz gauge\n'
  printf '# HELP openwrt_wifi_tx_power_dbm WiFi transmit power in dBm per AP interface.\n'
  printf '# TYPE openwrt_wifi_tx_power_dbm gauge\n'
  printf '# HELP openwrt_wifi_noise_dbm WiFi noise floor in dBm per AP interface.\n'
  printf '# TYPE openwrt_wifi_noise_dbm gauge\n'
  printf '# HELP openwrt_wifi_quality_percent WiFi quality percent per AP interface from iwinfo.\n'
  printf '# TYPE openwrt_wifi_quality_percent gauge\n'

  if command -v iwinfo >/dev/null 2>&1; then
    printf 'openwrt_wifi_radio_collector_available 1\n'

    iwinfo 2>/dev/null | awk '
      function emit_all() {
        if (ifname == "") {
          return
        }

        if (channel != "") {
          printf "openwrt_wifi_channel{ifname=\"%s\"} %s\n", ifname, channel
        }
        if (freq_hz != "") {
          printf "openwrt_wifi_frequency_hz{ifname=\"%s\"} %s\n", ifname, freq_hz
        }
        if (tx_dbm != "") {
          printf "openwrt_wifi_tx_power_dbm{ifname=\"%s\"} %s\n", ifname, tx_dbm
        }
        if (noise_dbm != "") {
          printf "openwrt_wifi_noise_dbm{ifname=\"%s\"} %s\n", ifname, noise_dbm
        }
        if (quality_pct != "") {
          printf "openwrt_wifi_quality_percent{ifname=\"%s\"} %s\n", ifname, quality_pct
        }
      }

      /^[^[:space:]]+[[:space:]]+ESSID:/ {
        emit_all()
        ifname = $1
        channel = ""
        freq_hz = ""
        tx_dbm = ""
        noise_dbm = ""
        quality_pct = ""
        next
      }

      /Channel:/ {
        channel = ""
        freq_hz = ""

        for (i = 1; i <= NF; i++) {
          if ($i == "Channel:" && i + 1 <= NF) {
            channel = $(i + 1)
            gsub(/[^0-9]/, "", channel)
          }

          token = $i
          gsub(/[()]/, "", token)
          if (token ~ /GHz$/) {
            gsub(/GHz$/, "", token)
            if (token + 0 > 0) {
              freq_hz = int((token + 0) * 1000000000)
            }
          }
        }
        next
      }

      /Tx-Power:/ {
        for (i = 1; i <= NF; i++) {
          if ($i == "Tx-Power:" && i + 1 <= NF) {
            tx_dbm = $(i + 1)
            gsub(/[^0-9.\-]/, "", tx_dbm)
          }
        }
        next
      }

      /Noise:/ {
        for (i = 1; i <= NF; i++) {
          if ($i == "Noise:" && i + 1 <= NF) {
            noise_dbm = $(i + 1)
            gsub(/[^0-9.\-]/, "", noise_dbm)
          }
        }
        next
      }

      /Quality:/ {
        for (i = 1; i <= NF; i++) {
          if ($i == "Quality:" && i + 1 <= NF) {
            split($(i + 1), q, "/")
            if ((q[2] + 0) > 0) {
              quality_pct = sprintf("%.2f", (q[1] + 0) * 100 / (q[2] + 0))
            }
          }
        }
      }

      END {
        emit_all()
      }
    '
  else
    printf 'openwrt_wifi_radio_collector_available 0\n'
  fi
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
