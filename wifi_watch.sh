#!/usr/bin/env bash
# Log the WiFi link every few seconds and flag the "downlink stuck at 6 Mbit/s" state.
# With HEAL=1 it also reconnects when the downlink stays stuck while the signal is good.
# Usage: [HEAL=1] wifi_watch.sh [interval_s] [duration_h]
# Stop with: pkill -f 'bash .*wifi_watch.sh'
IFACE=$(iw dev | awk '$1 == "Interface" {print $2; exit}')   # first WiFi interface
INTERVAL=${1:-5}
DURATION_H=${2:-10}
HEAL=${HEAL:-0}
DATA_DIR="$HOME/.system_doctor"   # the data folder the other system_doctor tools use
LOG="$DATA_DIR/wifi_watch.log"
PIDFILE="$DATA_DIR/wifi_watch.pid"
STUCK_RATE="6.0 MBit/s"
# Below this much traffic the "last rx rate" can come from idle frames sent at the basic rate.
MIN_TRAFFIC_KBPS=200
# Consecutive stuck samples before reconnecting, and minimum gap between two reconnections.
HEAL_AFTER_SAMPLES=3
HEAL_COOLDOWN_S=120
# 6 Mbit/s is only anomalous with a good signal; far from the AP it can be legitimate.
HEAL_MIN_SIGNAL_DBM=-70

rx_bytes() { cat "/sys/class/net/$IFACE/statistics/rx_bytes" 2>/dev/null || echo 0; }

heal() {
  local conn
  conn=$(nmcli -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null)
  echo "# HEAL $(date +%T) reconnecting '$conn' after $stuck_count stuck samples on $bssid" >> "$LOG"
  nmcli --wait 30 connection up "$conn" ifname "$IFACE" >/dev/null 2>&1
  echo "# HEAL $(date +%T) result=$? now=$(iw dev "$IFACE" link | awk '/^Connected to/ {print $3}')" >> "$LOG"
  last_heal=$(date +%s)
  stuck_count=0
  prev_rx=$(rx_bytes)
}

mkdir -p "$DATA_DIR"
echo $$ > "$PIDFILE"
# Remove the pid file when stopped (wifi_graph.sh exit, shutdown): a stale one outlives the reboot.
trap 'rm -f "$PIDFILE"; exit' INT TERM HUP
end=$(( $(date +%s) + DURATION_H * 3600 ))
prev_rx=$(rx_bytes)
stuck_count=0
last_heal=0
echo "# start $(date -Is) interval=${INTERVAL}s duration=${DURATION_H}h heal=$HEAL" >> "$LOG"
while [ "$(date +%s)" -lt "$end" ]; do
  sleep "$INTERVAL"
  rx=$(rx_bytes)
  kbps=$(( (rx - prev_rx) * 8 / 1000 / INTERVAL ))
  [ "$kbps" -lt 0 ] && kbps=0
  prev_rx=$rx
  link=$(iw dev "$IFACE" link 2>/dev/null)
  bssid=$(awk '/^Connected to/ {print $3}' <<<"$link")
  freq=$(awk '/freq:/ {print $2}' <<<"$link")
  sig=$(awk '/signal:/ {print $2}' <<<"$link")
  rxrate=$(sed -n 's/.*rx bitrate: //p' <<<"$link")
  txrate=$(sed -n 's/.*tx bitrate: //p' <<<"$link")
  flag=""
  if [ "$rxrate" = "$STUCK_RATE" ] && [ "$kbps" -ge "$MIN_TRAFFIC_KBPS" ]; then
    flag=" STUCK"
    stuck_count=$((stuck_count + 1))
  else
    stuck_count=0
  fi
  printf '%s bssid=%s freq=%s sig=%s rx_kbps=%s rx="%s" tx="%s"%s\n' \
    "$(date +%T)" "${bssid:-none}" "${freq:--}" "${sig:--}" "$kbps" "$rxrate" "$txrate" "$flag" >> "$LOG"
  if [ "$HEAL" = 1 ] && [ "$stuck_count" -ge "$HEAL_AFTER_SAMPLES" ] \
     && [ "${sig:--999}" -ge "$HEAL_MIN_SIGNAL_DBM" ] \
     && [ $(( $(date +%s) - last_heal )) -ge "$HEAL_COOLDOWN_S" ]; then
    heal
  fi
done
echo "# end $(date -Is)" >> "$LOG"
rm -f "$PIDFILE"
