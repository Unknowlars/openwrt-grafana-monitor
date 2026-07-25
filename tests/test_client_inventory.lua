-- Executes the real client_inventory collector against ubus/iwinfo/uci-shaped
-- fixtures. client_inventory.lua never calls a JSON decoder itself (ubus's
-- Lua binding hands back decoded tables directly), so this harness mocks
-- ubus/iwinfo/uci as Lua modules rather than faking file reads, and only
-- uses the json_to_lua.py bridge to build the mock data tables from the
-- checked-in JSON fixtures.

local root = (arg[0]:match("^(.*)/tests/") or ".")

local function load_fixture(path)
  local pipe = io.popen("python3 " .. root .. "/tests/json_to_lua.py " .. path)
  local literal = pipe:read("*a")
  pipe:close()
  local chunk = loadstring("return " .. literal)
  if not chunk then error("failed to load fixture: " .. path) end
  return chunk()
end

local GETHOSTHINTS = load_fixture(root .. "/tests/fixtures/gethosthints.json")
local WIRELESS_STATUS = load_fixture(root .. "/tests/fixtures/wireless_status.json")
local ASSOCLIST = load_fixture(root .. "/tests/fixtures/assoclist.json")

-- The router's own interface addresses, as `cat /sys/class/net/*/address`
-- would report them -- confirmed live (2026-07-22) that getHostHints
-- includes the router's own br-lan identity as if it were a client, which
-- router_own_macs() in the collector is meant to filter back out.
local ROUTER_OWN_MAC = "60:cf:84:f2:a4:30"

-- Mutable per-test knobs, reset by reset_mocks().
local MOCK

local function reset_mocks()
  MOCK = {
    gethosthints = GETHOSTHINTS,
    wireless_status = WIRELESS_STATUS,
    assoclist = ASSOCLIST,
    dhcp_hosts = {{mac = "AA:BB:CC:DD:EE:01"}},
    network_interfaces = {{[".name"] = "lan", device = "br-lan"}},
    firewall_defaults = {{flow_offloading = "0", flow_offloading_hw = "1"}},
    ubus_connect_fails = false,
    gethosthints_fails = false,
    wireless_status_fails = false,
    leasefile_missing = false,
    conf_max = nil,
    firewall_cursor_fail_remaining = 0,
  }
end
reset_mocks()

package.preload["ubus"] = function()
  return {
    connect = function()
      if MOCK.ubus_connect_fails then return nil end
      return {
        call = function(_, obj, method)
          if obj == "luci-rpc" and method == "getHostHints" then
            if MOCK.gethosthints_fails then return nil end
            return MOCK.gethosthints
          end
          if obj == "network.wireless" and method == "status" then
            if MOCK.wireless_status_fails then return nil end
            return MOCK.wireless_status
          end
          return nil
        end,
        close = function() end,
      }
    end,
  }
end

package.preload["iwinfo"] = function()
  local backend = {
    assoclist = function(ifname) return MOCK.assoclist[ifname] or {} end,
  }
  return {
    type = function() return "nl80211" end,
    nl80211 = backend,
  }
end

package.preload["uci"] = function()
  return {
    cursor = function()
      return {
        foreach = function(_, config, sectiontype, callback)
          if config == "firewall" and sectiontype == "defaults" then
            -- Simulates the live-audit finding (2026-07-23): the firewall
            -- defaults read intermittently fails. Each call consumes one
            -- simulated failure so tests can exercise both "fails within the
            -- retry budget, then succeeds" and "fails every time".
            if MOCK.firewall_cursor_fail_remaining > 0 then
              MOCK.firewall_cursor_fail_remaining = MOCK.firewall_cursor_fail_remaining - 1
              error("simulated transient uci read failure")
            end
            for _, section in ipairs(MOCK.firewall_defaults) do callback(section) end
          elseif config == "dhcp" and sectiontype == "host" then
            for _, section in ipairs(MOCK.dhcp_hosts) do callback(section) end
          elseif config == "network" and sectiontype == "interface" then
            for _, section in ipairs(MOCK.network_interfaces) do callback(section) end
          end
        end,
      }
    end,
  }
end

-- /etc/openwrt-client-seen (plus its .tmp.<ts> staging path) is redirected to
-- a real, disposable temp directory so persistence/LRU eviction can be
-- exercised against a real filesystem without touching the actual /etc.
local TMP_SEEN_DIR = os.tmpname()
os.remove(TMP_SEEN_DIR)
os.execute("mkdir -p '" .. TMP_SEEN_DIR .. "'")

local function rewrite_seen_path(path)
  local suffix = path:match("^/etc/(openwrt%-client%-seen.*)$")
  if suffix then return TMP_SEEN_DIR .. "/" .. suffix end
  return path
end

local real_open = io.open
local real_rename = os.rename
io.open = function(path, mode)
  path = rewrite_seen_path(path)
  if path == "/tmp/dhcp.leases" then
    if MOCK.leasefile_missing then return nil end
    return real_open(root .. "/tests/fixtures/dhcp.leases", mode)
  end
  if path == "/proc/net/arp" then
    return real_open(root .. "/tests/fixtures/proc-net-arp.txt", mode)
  end
  if path == "/proc/sys/kernel/hostname" then
    return real_open(root .. "/tests/fixtures/hostname.txt", mode)
  end
  if path == "/etc/openwrt-grafana-monitor.conf" then
    if MOCK.conf_max then
      local tmp = TMP_SEEN_DIR .. "/conf"
      local f = real_open(tmp, "w")
      f:write('CLIENT_INVENTORY_MAX="' .. MOCK.conf_max .. '"\n')
      f:close()
      return real_open(tmp, mode)
    end
    return nil
  end
  return real_open(path, mode)
end
os.rename = function(old, new)
  return real_rename(rewrite_seen_path(old), rewrite_seen_path(new))
end

local real_popen = io.popen
io.popen = function(command, mode)
  if command:match("^cat /sys/class/net/") then
    return real_popen("printf '%s\\n' '" .. ROUTER_OWN_MAC .. "'")
  end
  return real_popen(command, mode)
end

local samples
function metric(name)
  return function(labels, value)
    local parts = {}
    for key, val in pairs(labels or {}) do
      parts[#parts + 1] = key .. '="' .. tostring(val) .. '"'
    end
    table.sort(parts)
    samples[#samples + 1] = {name = name, labels = "{" .. table.concat(parts, ",") .. "}", value = value, raw = labels or {}}
  end
end

local function run()
  samples = {}
  local collector = dofile(root .. "/openwrt/collectors/client_inventory.lua")
  collector.scrape()

  local index = {}
  for _, sample in ipairs(samples) do
    index[sample.name] = index[sample.name] or {}
    table.insert(index[sample.name], sample)
  end
  return index
end

local function by_mac(list, mac)
  for _, sample in ipairs(list or {}) do
    if sample.raw.mac == mac then return sample end
  end
  return nil
end

local failures = 0
local function check(condition, message)
  if not condition then
    print("FAIL: " .. message)
    failures = failures + 1
  end
end

-- 1. Full success path: ubus + iwinfo + uci all working. ----------------------
reset_mocks()
local full = run()

check(full["openwrt_client_inventory_collector_available"][1].value == 1,
  "full success reports available")
-- The fixture has 6 getHostHints entries; the 6th is the router's own
-- br-lan identity (mirroring what getHostHints actually returns live), which
-- router_own_macs() must filter back out.
check(#full["openwrt_client_info"] == 5,
  "one info series per known client, router's own identity excluded")
check(by_mac(full["openwrt_client_info"], ROUTER_OWN_MAC) == nil,
  "the router's own interface mac is never emitted as a client")

local seen_keys = {}
for _, sample in ipairs(full["openwrt_client_info"]) do
  check(not seen_keys[sample.labels], "duplicate client_info series: " .. sample.labels)
  seen_keys[sample.labels] = true
  check(sample.raw.mac:match("^%x%x:%x%x:%x%x:%x%x:%x%x:%x%x$") ~= nil,
    "mac label is lowercase colon-separated: " .. tostring(sample.raw.mac))
  check(sample.raw.hostname ~= nil and sample.raw.hostname ~= "",
    "hostname is never empty: " .. sample.labels)
end

local tv = by_mac(full["openwrt_client_info"], "a4:83:e7:aa:bb:cc")
check(tv ~= nil, "uppercase getHostHints mac normalised to lowercase")
check(tv and tv.raw.hostname == "living-room-tv", "hostname from getHostHints")
check(tv and tv.raw.connection == "wifi", "associated station is wifi")
check(tv and tv.raw.ssid == "Home-5G", "ssid read from iface.config.ssid")
check(tv and tv.raw.band == "5g", "band read from radio.config.band")
check(tv and tv.raw.network == "lan", "network read from iface.config.network")
check(tv and tv.raw.has_ipv6 == "1", "two ip6addrs -> has_ipv6=1")
check(tv and tv.raw.mac_type == "global", "a4:... is not locally administered")
check(by_mac(full["openwrt_client_up"], "a4:83:e7:aa:bb:cc").value == 1, "wifi client is up")

local phone = by_mac(full["openwrt_client_info"], "2a:11:22:33:44:55")
check(phone and phone.raw.network == "guest", "guest ssid maps to guest network")
check(phone and phone.raw.mac_type == "local", "2a:... is locally administered (randomised mac)")

local c100 = by_mac(full["openwrt_client_info"], "78:8c:b5:93:fb:a9")
check(c100 and c100.raw.connection == "wired", "non-associated mac with working assoclist data is wired, not unknown")
check(c100 and c100.raw.network == "lan", "wired client network resolves from ARP device to UCI network")
check(c100 and c100.raw.hostname == "C100", "hostname from leasefile-matched getHostHints entry")
check(by_mac(full["openwrt_client_up"], "78:8c:b5:93:fb:a9").value == 1,
  "wired client reachable in /proc/net/arp is up")
check(by_mac(full["openwrt_client_lease_expiry_seconds"], "78:8c:b5:93:fb:a9").value == 1784822400,
  "lease expiry read from dhcp.leases")

local unnamed = by_mac(full["openwrt_client_info"], "f4:a3:10:5a:e9:74")
check(unnamed and unnamed.raw.hostname == "unknown_f4a3105ae974",
  "missing getHostHints name falls back to an explicit unknown_<mac> label, never empty")
check(by_mac(full["openwrt_client_up"], "f4:a3:10:5a:e9:74").value == 0,
  "wired client absent from /proc/net/arp is not up")
check(unnamed and unnamed.raw.network == "unknown",
  "wired client without an ARP-device mapping is explicitly unknown, not guessed as lan")

local printer = by_mac(full["openwrt_client_info"], "aa:bb:cc:dd:ee:01")
check(printer and printer.raw.static == "1", "uci dhcp.host match sets static=1")
check(by_mac(full["openwrt_client_lease_expiry_seconds"], "aa:bb:cc:dd:ee:01").value == 0,
  "static lease with no leasefile entry reports expiry 0 (infinite), not fabricated")

local offload = full["openwrt_flow_offload_enabled"]
check(#offload == 2, "both sw and hw offload series exported")
for _, sample in ipairs(offload) do
  if sample.raw.mode == "sw" then check(sample.value == 0, "sw offload reported off") end
  if sample.raw.mode == "hw" then check(sample.value == 1, "hw offload reported on") end
end
check(full["openwrt_flow_offload_read_success"][1].value == 1,
  "offload read success is always reported, and true on a clean read")

check(full["openwrt_client_inventory_truncated"][1].value == 0, "not truncated under the default cap")

-- 1b. Firewall UCI read fails twice then succeeds: the retry added in the
--     2026-07-23 live-audit fix must recover within the same scrape, not
--     just on a later one. -------------------------------------------------
reset_mocks()
MOCK.firewall_cursor_fail_remaining = 2
local retried = run()
check(retried["openwrt_flow_offload_read_success"][1].value == 1,
  "read succeeds within the retry budget after two transient failures")
local retried_offload = retried["openwrt_flow_offload_enabled"]
check(#retried_offload == 2, "offload series still exported once the retry succeeds")

-- 1c. Firewall UCI read fails every attempt: must not silently omit the
--     metric as if it were a truthful zero -- report read failure instead. --
reset_mocks()
MOCK.firewall_cursor_fail_remaining = 99
local unreadable = run()
check(unreadable["openwrt_flow_offload_read_success"][1].value == 0,
  "permanent uci read failure is reported, not silently swallowed")
check(unreadable["openwrt_flow_offload_enabled"] == nil,
  "no offload sample is emitted when the read never succeeds -- absence, not a false zero")
check(unreadable["openwrt_client_inventory_collector_available"][1].value == 1,
  "an unrelated firewall-read failure must not fail the whole collector closed")

-- 2. getHostHints fails but the ubus socket is otherwise up: leasefile
--    identity, but wireless join (ap/ssid/band/connection) still works,
--    because it uses the same live connection, not getHostHints. -----------
reset_mocks()
MOCK.gethosthints_fails = true
local partial = run()
check(partial["openwrt_client_inventory_collector_available"][1].value == 1,
  "leasefile fallback still reports available")
local tv2 = by_mac(partial["openwrt_client_info"], "78:8c:b5:93:fb:a9")
check(tv2 ~= nil, "leasefile fallback still identifies known leased clients")
check(tv2 and tv2.raw.hostname == "C100", "leasefile hostname used as fallback identity")

-- 3. Total ubus failure and no leasefile: a genuine functional gap, not a
--    workaround opportunity -- collector must report unavailable with zero
--    partial series, matching the "killed mid-run" acceptance criterion. ---
reset_mocks()
MOCK.ubus_connect_fails = true
MOCK.leasefile_missing = true
local dead = run()
check(dead["openwrt_client_inventory_collector_available"][1].value == 0,
  "no identity source at all reports unavailable")
check(dead["openwrt_client_info"] == nil, "zero partial client_info series when unavailable")
check(dead["openwrt_client_up"] == nil, "zero partial client_up series when unavailable")

-- 4. Truncation: cap below the known host count. -----------------------------
reset_mocks()
MOCK.conf_max = "3"
local capped = run()
check(#capped["openwrt_client_info"] == 3, "emission capped at CLIENT_INVENTORY_MAX")
check(capped["openwrt_client_inventory_truncated"][1].value == 1,
  "truncated flag set when the host count exceeds the cap")
-- Deterministic which 3: sorted mac order, so the same macs truncate the
-- same way every scrape rather than churning.
local capped_macs = {}
for _, sample in ipairs(capped["openwrt_client_info"]) do capped_macs[#capped_macs + 1] = sample.raw.mac end
table.sort(capped_macs)
check(capped_macs[1] == "2a:11:22:33:44:55", "capped set is the lowest macs in sorted order")

-- 5. SSID label values are sanitized, and identically to the shell path. ------
-- R6: this collector used to emit the raw ubus SSID while
-- openwrt-monitor-client-conntrack.sh emitted a sanitized one, so one physical
-- SSID appeared under two different label values and any join between
-- openwrt_client_info and the assoc-event metrics returned nothing. A raw `"`
-- also corrupts the exposition line outright.
--
-- `wlan3` in the shared tests/fixtures/wireless_status.json fixture is named
-- `Lab Net"5G` for exactly this: a space and a double quote. The expected
-- sanitized value below must stay identical to the one asserted in
-- tests/test_client_conntrack.sh -- that pairing is what keeps the two paths
-- converged.
local EXPECTED_SANITIZED_SSID = 'Lab_Net_5G'

reset_mocks()
MOCK.assoclist = {wlan3 = {["a4:83:e7:aa:bb:cc"] = {signal = -70}}}
local sanitized = run()
local on_wlan3 = by_mac(sanitized["openwrt_client_info"], "a4:83:e7:aa:bb:cc")
check(on_wlan3 ~= nil, "client associated to wlan3 is emitted")
check(on_wlan3 and on_wlan3.raw.ssid == EXPECTED_SANITIZED_SSID,
  "ssid is sanitized to " .. EXPECTED_SANITIZED_SSID .. ", got " ..
  tostring(on_wlan3 and on_wlan3.raw.ssid))
for _, sample in ipairs(sanitized["openwrt_client_info"]) do
  check(not tostring(sample.raw.ssid):find('"', 1, true),
    "no raw double quote reaches an ssid label value: " .. sample.labels)
  check(not tostring(sample.raw.ssid):find(' ', 1, true),
    "no raw space reaches an ssid label value: " .. sample.labels)
end

-- 6. first_seen persists across scrapes and evicts LRU when the store
--    (not just one scrape's host count) exceeds the cap. ---------------------
-- Starts from a clean seen-store: os.time() has 1-second resolution, and
-- sections 1-4 above ran fast enough that their entries could tie on `last`
-- with this section's, which would make the alphabetical tie-break (not
-- recency) decide eviction. A real router scrapes tens of seconds apart, so
-- this is purely a same-second test artifact -- isolate it instead of
-- padding every earlier section with sleeps.
os.execute("rm -rf '" .. TMP_SEEN_DIR .. "'; mkdir -p '" .. TMP_SEEN_DIR .. "'")
reset_mocks()
MOCK.conf_max = "2"
MOCK.gethosthints = {["78:8c:b5:93:fb:a9"] = GETHOSTHINTS["78:8c:b5:93:fb:a9"], ["f4:a3:10:5a:e9:74"] = GETHOSTHINTS["f4:a3:10:5a:e9:74"]}
local first_run = run()
local first_seen_1 = by_mac(first_run["openwrt_client_first_seen_seconds"], "78:8c:b5:93:fb:a9").value

os.execute("sleep 1")
local second_run = run()
local first_seen_2 = by_mac(second_run["openwrt_client_first_seen_seconds"], "78:8c:b5:93:fb:a9").value
check(first_seen_1 == first_seen_2, "first_seen is stable across scrapes for the same mac")

-- A third scrape with two different macs pushes the store to 4 distinct
-- entries against a cap of 2: the two macs from the first two runs must be
-- evicted (oldest last-seen), so if 78:8c:... reappears later it looks new.
MOCK.gethosthints = {["a4:83:e7:aa:bb:cc"] = GETHOSTHINTS["A4:83:E7:AA:BB:CC"], ["aa:bb:cc:dd:ee:01"] = GETHOSTHINTS["aa:bb:cc:dd:ee:01"]}
run()

os.execute("sleep 1")
MOCK.gethosthints = {["78:8c:b5:93:fb:a9"] = GETHOSTHINTS["78:8c:b5:93:fb:a9"]}
local fourth_run = run()
local first_seen_4 = by_mac(fourth_run["openwrt_client_first_seen_seconds"], "78:8c:b5:93:fb:a9").value
check(first_seen_4 > first_seen_1,
  "mac evicted by LRU cap gets a fresh first_seen when it reappears, proving eviction happened")

os.execute("rm -rf '" .. TMP_SEEN_DIR .. "'")
io.open = real_open
os.rename = real_rename
io.popen = real_popen

if failures == 0 then
  print("PASS: tests/test_client_inventory.lua (" .. (#full["openwrt_client_info"]) .. " clients in the full-success run)")
  os.exit(0)
end
print(failures .. " failure(s)")
os.exit(1)
