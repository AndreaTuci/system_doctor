#!/usr/bin/env bash
# Live terminal chart of the WiFi downlink rate (AP -> PC), one column per wifi_watch.sh sample.
# Red columns = downlink stuck at 6 Mbit/s, the office WiFi block: press w to reconnect (= wifi-fix).
# If wifi_watch.sh is not running it is started here (log only) and stopped on exit.
# Usage: wifi_graph.sh   (w = reconnect, Ctrl+C = quit)      Check: wifi_graph.sh --selftest
set -u

DATA_DIR="$HOME/.system_doctor"   # the data folder the other system_doctor tools use
LOG="$DATA_DIR/wifi_watch.log"
PIDFILE="$DATA_DIR/wifi_watch.pid"
WATCH="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/wifi_watch.sh"   # ships next to this script
SAMPLE_SECONDS=5
REFRESH_SECONDS=1
STALE_AFTER_SAMPLES=3      # log not written for this long = monitor stopped
OWN_MONITOR_HOURS=24       # a monitor started here is stopped on exit anyway
STUCK_RATE="6.0 MBit/s"    # legacy rate the AP falls back to during the block
SCALE_MIN_MBIT=6
SCALE_MAX_MBIT=867         # VHT80 2x2 maximum
MAX_ROWS=12
MIN_ROWS=3
CHROME_ROWS=7              # header 3 + axis 2 + legend 1 + one spare
LABEL_EVERY_ROWS=3
LABEL_COLUMNS=5            # 4-digit y label + axis glyph
WIFI_PROFILE="Lotrèk Uffici"   # the connection the wifi-fix alias brings back up
FIX_KEY="w"
FIX_FAILED_SECONDS=15      # how long a failed reconnect stays on screen

own_monitor_pid=""
fix_pid=""                 # background reconnect started with FIX_KEY, while it runs
fix_failed_until=0
saved_tty=""

# Reads wifi_watch.log lines on stdin, prints one full frame. A single 6 Mbit/s sample is drawn
# as a normal minimum-height bar (idle links show it now and then); two or more in a row turn red.
read -r -d '' RENDER <<'AWK'
BEGIN {
  green = "\033[32m"; yellow = "\033[33m"; red = "\033[91m"; gray = "\033[90m"
  bold = "\033[1m"; reset = "\033[0m"; clr = "\033[K"
  split(" ,▁,▂,▃,▄,▅,▆,▇,█", blk, ",")
  span = log(hi) - log(lo)
}
/^# start/ { n = 0; next }
/^#/ { next }
{
  match($0, /rx="[^"]*"/); rx[n] = substr($0, RSTART + 4, RLENGTH - 5)
  t[n] = $1; node[n] = substr($2, length($2) - 4); sig[n] = substr($4, 5)
  st[n] = ($2 == "bssid=none") ? "off" : (rx[n] == stuck ? "stuck" : "ok")
  if (st[n] == "stuck" && n > 0 && (st[n - 1] == "stuck" || st[n - 1] == "down")) st[n] = st[n - 1] = "down"
  n++
}
END {
  for (i = 0; i < n; i++) if (st[i] == "stuck") st[i] = "blip"
  print_header(); print_chart(); print_axis()
  printf "%s■%s ok  %s■%s bloccato  ■ spento    w = riconnetti%s%s\n", green, gray, red, gray, reset, clr
}
function print_header(   last, run, i, status) {
  if (n == 0) { printf "%sWiFi%s%s\n%s● in attesa del primo campione...%s%s\n%s\n", bold, reset, clr, gray, reset, clr, clr; return }
  last = n - 1
  if (st[last] == "off") printf "%s%s%s%s\n", bold, t[last], reset, clr
  else printf "%s%s  %s dBm  %s  %s%s%s\n", bold, node[last], sig[last], mbit_text(rx[last]), t[last], reset, clr
  run = 0
  for (i = last; i >= 0 && st[i] == "down"; i--) run++
  if (notice == "fixing") status = yellow "● riconnessione in corso..."
  else if (notice == "failed") status = red bold "● riconnessione non riuscita: riprova con w"
  else if (stale) status = gray "● monitor fermo: i dati non si aggiornano"
  else if (st[last] == "down") status = red bold "● BLOCCATO da " duration_text(run * interval) ": premi w"
  else if (st[last] == "blip") status = yellow "● ricezione al minimo, verifico al prossimo campione"
  else if (st[last] == "off") status = gray "● non connesso"
  else status = green "● OK"
  printf "%s%s%s\n%s\n", status, reset, clr, clr
}
function print_chart(   r, i, line) {
  for (r = rows; r >= 1; r--) {
    line = gray (((rows - r) % label_every == 0) ? sprintf("%4d┤", exp(log(lo) + r / rows * span)) : "    │")
    for (i = 0; i < n; i++) line = line cell(i, r)
    print line reset clr
  }
}
function cell(i, r,   eighths) {
  if (st[i] == "down") return red "█"
  if (st[i] == "off") return gray "░"
  eighths = bar_eighths(rx[i] + 0)
  if (eighths >= r * 8) return green "█"
  if (eighths > (r - 1) * 8) return green blk[eighths - (r - 1) * 8 + 1]
  return " "
}
function bar_eighths(mbit,   share, eighths) {
  if (mbit <= lo) return 1
  share = (log(mbit) - log(lo)) / span
  eighths = int((share > 1 ? 1 : share) * rows * 8 + 0.5)
  return eighths < 1 ? 1 : eighths
}
function print_axis(   i, ticks, labels, minute, previous) {
  ticks = ""; labels = ""
  for (i = 0; i < n; i++) {
    minute = substr(t[i], 1, 5)
    if (i > 0 && minute != previous) {
      ticks = ticks "┬"
      if (i >= length(labels)) labels = labels sprintf("%" (i - length(labels)) "s", "") minute
    } else ticks = ticks "─"
    previous = minute
  }
  printf "%s%4d└%s%s%s\n%s     %s%s%s\n", gray, lo, ticks, reset, clr, gray, labels, reset, clr
}
function mbit_text(r) { return r == "" ? "-" : sprintf("%d Mbit/s", r + 0) }
function duration_text(s) { return s >= 60 ? sprintf("%dm %02ds", int(s / 60), s % 60) : sprintf("%ds", s) }
AWK

render() {  # rows stale notice  < log lines
  awk -v rows="$1" -v stale="$2" -v notice="$3" -v interval="$SAMPLE_SECONDS" -v stuck="$STUCK_RATE" \
      -v lo="$SCALE_MIN_MBIT" -v hi="$SCALE_MAX_MBIT" -v label_every="$LABEL_EVERY_ROWS" "$RENDER"
}

draw_frame() {  # session_offset: log size when the chart was opened, older lines are not shown
  local session_offset=$1 rows width stale=0 notice=""
  rows=$(( $(tput lines) - CHROME_ROWS ))
  (( rows > MAX_ROWS )) && rows=$MAX_ROWS
  (( rows < MIN_ROWS )) && rows=$MIN_ROWS
  width=$(( $(tput cols) - LABEL_COLUMNS ))
  (( $(date +%s) - $(stat -c %Y "$LOG") > STALE_AFTER_SAMPLES * SAMPLE_SECONDS )) && stale=1
  [ -n "$fix_pid" ] && notice=fixing
  (( $(date +%s) < fix_failed_until )) && notice=failed
  printf '\e[H'
  tail -c +"$(( session_offset + 1 ))" "$LOG" | tail -n "$width" | render "$rows" "$stale" "$notice"
  printf '\e[J'
}

start_fix() {  # in the background so the chart keeps drawing; logged like the monitor's HEAL lines
  [ -n "$fix_pid" ] && return
  { msg=$(nmcli connection up "$WIFI_PROFILE" 2>&1); rc=$?
    echo "# FIX $(date +%T) result=$rc ${msg//$'\n'/ }" >> "$LOG"; exit "$rc"; } &
  fix_pid=$!
}

update_fix_state() {  # reaps a finished reconnect; a failure stays on screen for FIX_FAILED_SECONDS
  [ -n "$fix_pid" ] || return 0
  kill -0 "$fix_pid" 2>/dev/null && return 0
  wait "$fix_pid" || fix_failed_until=$(( $(date +%s) + FIX_FAILED_SECONDS ))
  fix_pid=""
}

next_key() {  # prints the key pressed within REFRESH_SECONDS, if any
  local key=""
  if [ -t 0 ]; then read -rsn1 -t "$REFRESH_SECONDS" key; else sleep "$REFRESH_SECONDS"; fi
  printf '%s' "$key"
}

monitor_running() {  # checks the command line too: after a reboot the old PID may belong to another process
  local pid
  pid=$(cat "$PIDFILE" 2>/dev/null) || return 1
  [ -r "/proc/$pid/cmdline" ] && [[ "$(tr '\0' ' ' < "/proc/$pid/cmdline")" == *wifi_watch.sh* ]]
}

start_monitor_if_needed() {
  monitor_running && return
  "$WATCH" "$SAMPLE_SECONDS" "$OWN_MONITOR_HOURS" >/dev/null 2>&1 &
  own_monitor_pid=$!
}

leave_screen() {
  [ -n "$saved_tty" ] && stty "$saved_tty"
  printf '\e[?7h\e[?25h'
  tput rmcup
  # Ctrl+C in a terminal reaches the monitor too, so it may already be gone
  [ -n "$own_monitor_pid" ] && kill -0 "$own_monitor_pid" 2>/dev/null && kill "$own_monitor_pid"
}

selftest() {
  local sample='%s bssid=52:ff:20:a2:63:10 freq=5540 sig=-46 rx_kbps=900 rx="%s" tx="780.0 MBit/s"\n'
  printf "$sample" 10:00:00 "780.0 MBit/s" 10:00:05 "$STUCK_RATE" 10:00:10 "$STUCK_RATE" 10:00:15 "$STUCK_RATE" \
    | render 6 0 "" | grep -q "BLOCCATO da 15s" || { echo "selftest FAILED: block not detected"; return 1; }
  printf "$sample" 10:00:00 "780.0 MBit/s" 10:00:05 "$STUCK_RATE" \
    | render 6 0 "" | grep -q "verifico" || { echo "selftest FAILED: lone 6 Mbit/s sample shown as block"; return 1; }
  echo "selftest ok"
}

main() {
  [ "${1:-}" = "--selftest" ] && { selftest; return; }
  local key session_offset
  mkdir -p "$DATA_DIR"
  : >> "$LOG"   # create it if missing, without refreshing its mtime (that would hide a stopped monitor)
  session_offset=$(stat -c %s "$LOG")
  start_monitor_if_needed
  trap leave_screen EXIT
  trap 'exit 130' INT TERM HUP
  tput smcup
  printf '\e[?25l\e[?7l'   # hide cursor, no line wrap: long lines get clipped instead of breaking the layout
  if [ -t 0 ]; then saved_tty=$(stty -g); stty -echo -icanon; fi   # keys act at once, nothing echoed
  while true; do
    update_fix_state
    draw_frame "$session_offset"
    key=$(next_key)
    [ "${key,,}" = "$FIX_KEY" ] && start_fix
  done
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
