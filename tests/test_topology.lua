-- Executes the real topology collector against the same ubus/iwinfo-shaped
-- fixtures test_client_inventory.lua uses (both collectors read the same
-- identity/association data, see topology.lua's header comment), and checks
-- the two acceptance criteria the plan calls out explicitly for M5:
-- every edge source/target resolves to a node id, and every arc__* group
-- sums to 1. It also exercises the disappearing-client case, since a
-- dangling edge endpoint crashes Grafana's node graph panel rather than
-- rendering incompletely (plan §2.1).

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
local ROUTER_OWN_MAC = "60:cf:84:f2:a4:30"

local MOCK

local function reset_mocks()
  MOCK = {
    gethosthints = GETHOSTHINTS,
    wireless_status = WIRELESS_STATUS,
    assoclist = ASSOCLIST,
    ubus_connect_fails = false,
    gethosthints_fails = false,
    leasefile_missing = false,
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

local real_open = io.open
io.open = function(path, mode)
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
  return real_open(path, mode)
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
    samples[#samples + 1] = {name = name, value = value, raw = labels or {}}
  end
end

local function run()
  samples = {}
  local collector = dofile(root .. "/openwrt/collectors/topology.lua")
  collector.scrape()

  local index = {}
  for _, sample in ipairs(samples) do
    index[sample.name] = index[sample.name] or {}
    table.insert(index[sample.name], sample)
  end
  return index
end

local function by_id(list, id)
  for _, sample in ipairs(list or {}) do
    if sample.raw.id == id then return sample end
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

-- 1. Full success path. -------------------------------------------------------
reset_mocks()
local full = run()
local nodes = full["openwrt_topology_node"] or {}
local edges = full["openwrt_topology_edge"] or {}

check(full["openwrt_topology_collector_available"][1].value == 1, "full run reports available")
check(#nodes > 0, "nodes were emitted")
check(#edges > 0, "edges were emitted")

check(by_id(nodes, "internet") ~= nil, "internet node present")
check(by_id(nodes, "router:openwrt-test") ~= nil, "router node present, hostname sanitized")
check(by_id(nodes, "ap:openwrt-test") ~= nil, "ap node present")
check(by_id(nodes, ROUTER_OWN_MAC) == nil, "router's own mac is never emitted as a client node")

local tv = by_id(nodes, "client:a4:83:e7:aa:bb:cc")
check(tv ~= nil, "wifi client node present")
check(tv and tv.raw.title == "living-room-tv", "client node title carries hostname")
check(tv and tv.raw.arc__online == "1" and tv.raw.arc__offline == "0", "associated wifi client is online")

local ssid5g = by_id(nodes, "ssid:Home-5G@5g")
check(ssid5g ~= nil, "ssid node present for Home-5G@5g")
check(ssid5g and tonumber(ssid5g.value) == 1, "ssid node value counts one associated station")

local ssid2g = by_id(nodes, "ssid:Home-2G@2g")
check(ssid2g ~= nil, "ssid node present even with zero associated stations")
check(ssid2g and tonumber(ssid2g.value) == 0, "ssid node with no associations reports a real zero, not omitted")

local wired = by_id(nodes, "client:78:8c:b5:93:fb:a9")
check(wired ~= nil, "wired reachable client node present")
check(wired and wired.raw.arc__online == "1", "wired client reachable in arp table is online")

local unreachable = by_id(nodes, "client:f4:a3:10:5a:e9:74")
check(unreachable ~= nil, "wired unreachable client node present")
check(unreachable and unreachable.raw.arc__online == "0" and unreachable.raw.arc__offline == "1",
  "wired client absent from arp table is offline")

-- 2. Every edge source/target resolves to a node id (the collector test the
--    plan's M5 acceptance criteria explicitly ask for). ----------------------
local node_ids = {}
for _, node in ipairs(nodes) do node_ids[node.raw.id] = true end
for _, edge in ipairs(edges) do
  check(node_ids[edge.raw.source] ~= nil, "edge " .. edge.raw.id .. " source resolves to a node: " .. tostring(edge.raw.source))
  check(node_ids[edge.raw.target] ~= nil, "edge " .. edge.raw.id .. " target resolves to a node: " .. tostring(edge.raw.target))
end

check(by_id(edges, "wan:openwrt-test") ~= nil, "wan edge present")
check(by_id(edges, "ap:openwrt-test") ~= nil, "router-to-ap edge present")
check(by_id(edges, "assoc:a4:83:e7:aa:bb:cc") ~= nil, "assoc edge present for wifi client")
check(by_id(edges, "lan:78:8c:b5:93:fb:a9") ~= nil, "lan edge present for wired client")
check(by_id(edges, "assoc:78:8c:b5:93:fb:a9") == nil, "wired client has no assoc edge")
check(by_id(edges, "lan:a4:83:e7:aa:bb:cc") == nil, "wifi client has no lan edge")

-- 3. Every arc__* group sums to 1. ---------------------------------------------
for _, node in ipairs(nodes) do
  local sum = 0
  local has_arc = false
  for key, value in pairs(node.raw) do
    if key:match("^arc__") then
      has_arc = true
      sum = sum + tonumber(value)
    end
  end
  if has_arc then
    check(math.abs(sum - 1) < 1e-9, "arc__ group sums to 1 for node " .. tostring(node.raw.id) .. " (got " .. sum .. ")")
  end
end

-- 4. Disappearing client: a client that vanishes from getHostHints (e.g. it
--    left the network) must not leave a dangling edge referencing it. --------
reset_mocks()
MOCK.gethosthints = {}
for mac, entry in pairs(GETHOSTHINTS) do
  if mac ~= "A4:83:E7:AA:BB:CC" then MOCK.gethosthints[mac] = entry end
end
-- A client that actually left is gone from the live assoclist too, not just
-- from getHostHints -- keep the fixture realistic instead of leaving a
-- dangling association behind for the station-count check below.
MOCK.assoclist = {wlan0 = {}, wlan1 = {}, wlan2 = ASSOCLIST.wlan2}
local after_disconnect = run()
local nodes2 = after_disconnect["openwrt_topology_node"] or {}
local edges2 = after_disconnect["openwrt_topology_edge"] or {}

check(by_id(nodes2, "client:a4:83:e7:aa:bb:cc") == nil, "disconnected client node no longer emitted")
check(by_id(edges2, "assoc:a4:83:e7:aa:bb:cc") == nil, "disconnected client's assoc edge no longer emitted")

local node_ids2 = {}
for _, node in ipairs(nodes2) do node_ids2[node.raw.id] = true end
for _, edge in ipairs(edges2) do
  check(node_ids2[edge.raw.source] ~= nil, "post-disconnect edge " .. edge.raw.id .. " source still resolves")
  check(node_ids2[edge.raw.target] ~= nil, "post-disconnect edge " .. edge.raw.id .. " target still resolves")
end

-- The SSID that client was the only station on drops to zero, not to a
-- vanished node -- the ssid/radio topology is still real even with nobody
-- connected to it.
local ssid5g_after = by_id(nodes2, "ssid:Home-5G@5g")
check(ssid5g_after ~= nil, "ssid node survives its last client disconnecting")
check(ssid5g_after and tonumber(ssid5g_after.value) == 0, "ssid station count drops to zero, not fabricated")

-- 5. Total failure: no identity source at all reports unavailable with zero
--    partial series, matching client_inventory.lua's convention. -------------
reset_mocks()
MOCK.ubus_connect_fails = true
MOCK.leasefile_missing = true
local dead = run()
check(dead["openwrt_topology_collector_available"][1].value == 0, "no identity source reports unavailable")
check(dead["openwrt_topology_node"] == nil, "zero partial node series when unavailable")
check(dead["openwrt_topology_edge"] == nil, "zero partial edge series when unavailable")

io.open = real_open
io.popen = real_popen

if failures == 0 then
  print("PASS: tests/test_topology.lua (" .. #nodes .. " nodes, " .. #edges .. " edges in the full-success run)")
  os.exit(0)
end
print(failures .. " failure(s)")
os.exit(1)
