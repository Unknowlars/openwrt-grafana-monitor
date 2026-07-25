-- Executes the real topology collector against the same ubus/iwinfo-shaped
-- fixtures test_client_inventory.lua uses (both collectors read the same
-- identity/association data, see topology.lua's header comment), in both of
-- the deployment roles the collector supports.
--
-- The standing acceptance criteria (plan §2.1, M5) are checked in every
-- scenario: every edge source/target must resolve to a node id emitted in the
-- same scrape, and every arc__* group must sum to 1. A dangling edge endpoint
-- crashes Grafana's node graph panel rather than rendering incompletely, so
-- the disappearing-client and role-transition cases are exercised explicitly.
--
-- The multi-router rules are what the rest of this file is about: a downstream
-- AP must not invent an uplink, must not claim other people's DHCP clients,
-- and must still emit enough placeholder nodes that its own association edges
-- resolve when the gateway's exporter is unreachable.

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
local BSSID = {
  wlan0 = "60:cf:84:f2:a4:31",
  wlan1 = "60:cf:84:f2:a4:32",
  wlan2 = "60:cf:84:f2:a4:33",
  wlan3 = "60:cf:84:f2:a4:34",
}
local OWN_MACS = {ROUTER_OWN_MAC, BSSID.wlan0, BSSID.wlan1, BSSID.wlan2, BSSID.wlan3}

local MOCK

local function reset_mocks()
  MOCK = {
    gethosthints = GETHOSTHINTS,
    wireless_status = WIRELESS_STATUS,
    assoclist = ASSOCLIST,
    ubus_connect_fails = false,
    gethosthints_fails = false,
    leasefile_missing = false,
    -- Gateway by default: a real WAN device, and a private WAN nexthop so the
    -- double-NAT hop is covered.
    role = "gateway",
    lan_status = {up = true, device = "br-lan", ["ipv4-address"] = {{address = "192.168.0.1", mask = 24}}},
    wan_status = {up = true, device = "wan", ["ipv4-address"] = {{address = "192.168.2.5", mask = 24}}},
    bridge_fdb_available = true,
    operstate = {lan1 = "up", lan2 = "down"},
  }
end
reset_mocks()

package.preload["ubus"] = function()
  return {
    connect = function()
      if MOCK.ubus_connect_fails then return nil end
      return {
        call = function(_, object, method, params)
          if object == "luci-rpc" and method == "getHostHints" then
            if MOCK.gethosthints_fails then return nil end
            return MOCK.gethosthints
          end
          if object == "network.wireless" and method == "status" then
            return MOCK.wireless_status
          end
          if object == "network.interface" and method == "status" then
            local name = type(params) == "table" and params.interface or nil
            if name == "lan" then return MOCK.lan_status end
            if name == "wan" then return MOCK.wan_status end
            return nil
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
    channel = function(ifname) return ifname == "wlan0" and 6 or 149 end,
  }
  return {
    type = function() return "nl80211" end,
    nl80211 = backend,
  }
end

package.preload["openwrt_oui_data"] = function()
  return dofile(root .. "/openwrt/lua/oui_data.lua")
end
package.preload["openwrt_oui"] = function()
  return dofile(root .. "/openwrt/lua/oui.lua")
end

local real_open = io.open

local function string_reader(text)
  -- Minimal stand-in for the handle topology.lua uses: read("*l") and lines().
  local position = 1
  local handle = {}
  function handle:read()
    if position > #text then return nil end
    local newline = text:find("\n", position, true)
    local line
    if newline then
      line = text:sub(position, newline - 1)
      position = newline + 1
    else
      line = text:sub(position)
      position = #text + 1
    end
    return line
  end
  function handle:lines()
    return function() return handle:read() end
  end
  function handle:close() end
  return handle
end

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
  local ifname = path:match("^/sys/class/net/(.+)/address$")
  if ifname then
    if BSSID[ifname] then return string_reader(BSSID[ifname] .. "\n") end
    return nil
  end
  local device = path:match("^/sys/class/net/(.+)/operstate$")
  if device then
    local state = MOCK.operstate[device]
    if state then return string_reader(state .. "\n") end
    return nil
  end
  return real_open(path, mode)
end

local real_popen = io.popen
io.popen = function(command, mode)
  if command:match("^cat /sys/class/net/") then
    return real_popen("printf '%s\\n' " .. table.concat(OWN_MACS, " "))
  end
  if command:match("^ip route show default") then
    local fixture = MOCK.role == "gateway" and "ip-route-gateway.txt" or "ip-route-ap.txt"
    return real_popen("cat " .. root .. "/tests/fixtures/" .. fixture)
  end
  if command:match("^bridge fdb show") then
    if not MOCK.bridge_fdb_available then return real_popen("true") end
    return real_popen("cat " .. root .. "/tests/fixtures/bridge-fdb.txt")
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

-- Shared invariants, run against every scenario rather than only the happy
-- path: these are the properties that decide whether the panel renders at all.
local function check_invariants(label, nodes, edges)
  local node_ids = {}
  for _, node in ipairs(nodes) do node_ids[node.raw.id] = true end
  for _, edge in ipairs(edges) do
    check(node_ids[edge.raw.source] ~= nil,
      label .. ": edge " .. tostring(edge.raw.id) .. " source resolves (" .. tostring(edge.raw.source) .. ")")
    check(node_ids[edge.raw.target] ~= nil,
      label .. ": edge " .. tostring(edge.raw.id) .. " target resolves (" .. tostring(edge.raw.target) .. ")")
  end

  for _, node in ipairs(nodes) do
    local sum, has_arc = 0, false
    for key, value in pairs(node.raw) do
      if key:match("^arc__") then
        has_arc = true
        sum = sum + tonumber(value)
      end
    end
    if has_arc then
      check(math.abs(sum - 1) < 1e-9,
        label .. ": arc__ group sums to 1 for " .. tostring(node.raw.id) .. " (got " .. sum .. ")")
    end
    check(node.raw.authority == "0" or node.raw.authority == "1",
      label .. ": node " .. tostring(node.raw.id) .. " declares an authority")
  end

  local seen = {}
  for _, node in ipairs(nodes) do
    check(seen[node.raw.id] == nil, label .. ": node id " .. tostring(node.raw.id) .. " emitted once")
    seen[node.raw.id] = true
  end
end

-- 1. Gateway role. -----------------------------------------------------------
reset_mocks()
local gateway = run()
local nodes = gateway["openwrt_topology_node"] or {}
local edges = gateway["openwrt_topology_edge"] or {}
local infra = gateway["openwrt_topology_infra_mac"] or {}

check(gateway["openwrt_topology_collector_available"][1].value == 1, "gateway run reports available")
check_invariants("gateway", nodes, edges)

check(by_id(nodes, "internet") ~= nil, "gateway emits the internet node")
check(by_id(nodes, "router:192.168.0.1") ~= nil, "router node is keyed by its LAN ip, not its hostname")
check(by_id(nodes, "router:openwrt-test") == nil, "hostname-keyed router id is gone")
check(by_id(nodes, "ap:openwrt-test") ~= nil, "ap node present")

local router_node = by_id(nodes, "router:192.168.0.1")
check(router_node and router_node.raw.authority == "1", "gateway owns the router node first-hand")

-- The upstream ISP box used to be drawn as an ordinary LAN client because it
-- sits in the ARP table like one.
local modem = by_id(nodes, "modem:192.168.2.1")
check(modem ~= nil, "private WAN nexthop becomes a modem node")
check(modem and modem.raw.title == "Sagemcom",
  "modem node is named from its OUI, got " .. tostring(modem and modem.raw.title))
check(by_id(edges, "wan:192.168.2.1") ~= nil, "internet connects to the modem")
check(by_id(edges, "nat:openwrt-test") ~= nil, "modem connects to the router (double NAT hop)")

-- BSS nodes are keyed by BSSID; two APs sharing an SSID no longer collide.
check(by_id(nodes, "bss:60:cf:84:f2:a4:32") ~= nil, "bss node present for wlan1")
check(by_id(nodes, "ssid:Home-5G@5g") == nil, "ssid-keyed node id is gone")
local bss_5g = by_id(nodes, "bss:60:cf:84:f2:a4:32")
check(bss_5g and bss_5g.raw.title == "Home-5G", "bss node title carries the ssid")
check(bss_5g and bss_5g.raw.subtitle == "5g ch149",
  "bss subtitle carries band and channel, got " .. tostring(bss_5g and bss_5g.raw.subtitle))
check(bss_5g and tonumber(bss_5g.value) == 1, "bss node counts its associated stations")
local bss_2g = by_id(nodes, "bss:60:cf:84:f2:a4:31")
check(bss_2g and tonumber(bss_2g.value) == 0, "bss with no stations reports a real zero, not omitted")
check(by_id(edges, "radio:60:cf:84:f2:a4:32") ~= nil, "ap-to-bss edge present")

-- Switch port tier.
check(by_id(nodes, "port:openwrt-test:lan1") ~= nil, "switch port node present from bridge fdb")
check(by_id(nodes, "port:openwrt-test:lan2") ~= nil, "second switch port node present")
local lan2 = by_id(nodes, "port:openwrt-test:lan2")
check(lan2 and lan2.raw.arc__warn == "1", "a port that is down is flagged, not shown as healthy")
check(by_id(edges, "link:openwrt-test:lan1") ~= nil, "router-to-port edge present")
check(by_id(nodes, "port:openwrt-test:wlan1") == nil, "wireless interfaces never become switch ports")

-- Client identity.
local tv = by_id(nodes, "client:a4:83:e7:aa:bb:cc")
check(tv ~= nil, "wifi client node present")
check(tv and tv.raw.title == "living-room-tv", "client title prefers its hostname")
check(tv and tv.raw.arc__online == "1", "associated wifi client is online")
check(tv and tv.raw.icon == "apple", "client icon comes from its OUI")

local unnamed = by_id(nodes, "client:f4:a3:10:5a:e9:74")
check(unnamed ~= nil, "client with no hostname still gets a node")
check(unnamed and unnamed.raw.title == "Apple 5a:e9:74",
  "nameless client is titled by vendor, got " .. tostring(unnamed and unnamed.raw.title))
check(unnamed and unnamed.raw.arc__offline == "1", "client absent from the arp table is offline")

local randomized = by_id(nodes, "client:2a:11:22:33:44:55")
check(randomized and randomized.raw.detail__mac_type == "local", "locally administered mac is reported as such")

check(by_id(nodes, "client:" .. ROUTER_OWN_MAC) == nil, "router's own mac is never emitted as a client node")

-- Wired clients hang off the port they are actually behind.
local wired_edge = by_id(edges, "lan:78:8c:b5:93:fb:a9")
check(wired_edge ~= nil, "wired client has a lan edge")
check(wired_edge and wired_edge.raw.source == "port:openwrt-test:lan1",
  "wired client attaches to its bridge port, got " .. tostring(wired_edge and wired_edge.raw.source))
-- No fdb entry for this one, so it falls back to the router rather than being
-- placed on a guessed port.
local no_fdb = by_id(edges, "lan:f4:a3:10:5a:e9:74")
check(no_fdb and no_fdb.raw.source == "router:192.168.0.1",
  "client with no fdb entry falls back to the router, got " .. tostring(no_fdb and no_fdb.raw.source))

check(by_id(edges, "assoc:a4:83:e7:aa:bb:cc") ~= nil, "assoc edge present for wifi client")
check(by_id(edges, "assoc:78:8c:b5:93:fb:a9") == nil, "wired client has no assoc edge")
check(by_id(edges, "lan:a4:83:e7:aa:bb:cc") == nil, "wifi client has no lan edge")

-- Infra MACs let the dashboard drop routers that appear as each other's
-- clients.
check(#infra == #OWN_MACS, "every own interface mac is published as infra, got " .. #infra)

-- 2. Association edge colour tracks signal. ----------------------------------
reset_mocks()
MOCK.assoclist = {
  wlan0 = {},
  wlan1 = {["a4:83:e7:aa:bb:cc"] = {signal = -55}},
  wlan2 = {["2a:11:22:33:44:55"] = {signal = -80}},
}
local signals = run()
local weak = by_id(signals["openwrt_topology_edge"], "assoc:2a:11:22:33:44:55")
local strong = by_id(signals["openwrt_topology_edge"], "assoc:a4:83:e7:aa:bb:cc")
check(strong and strong.raw.color == "green", "strong association edge is green")
check(weak and weak.raw.color == "red", "weak association edge is red, got " .. tostring(weak and weak.raw.color))

-- 2b. The upstream router is the modem hop, not also a client. --------------
--     It answers ARP on the WAN interface, so getHostHints lists it exactly
--     like a LAN client and it was briefly drawn twice (observed live).
reset_mocks()
MOCK.gethosthints = {}
for mac, entry in pairs(GETHOSTHINTS) do MOCK.gethosthints[mac] = entry end
MOCK.gethosthints["80:20:DA:4A:EB:47"] = {name = nil, ipaddrs = {"192.168.2.1"}, ip6addrs = {}}
local with_modem = run()
local wm_nodes = with_modem["openwrt_topology_node"] or {}
local wm_edges = with_modem["openwrt_topology_edge"] or {}
check_invariants("modem-in-hosthints", wm_nodes, wm_edges)
check(by_id(wm_nodes, "modem:192.168.2.1") ~= nil, "upstream router is still the modem node")
check(by_id(wm_nodes, "client:80:20:da:4a:eb:47") == nil,
  "upstream router is not also drawn as a lan client")
check(by_id(wm_edges, "lan:80:20:da:4a:eb:47") == nil, "upstream router gets no lan edge")

-- 3. Downstream AP role. -----------------------------------------------------
reset_mocks()
MOCK.role = "ap"
MOCK.wan_status = nil
MOCK.lan_status = {up = true, device = "br-lan", ["ipv4-address"] = {{address = "192.168.0.2", mask = 24}}}
local ap = run()
local ap_nodes = ap["openwrt_topology_node"] or {}
local ap_edges = ap["openwrt_topology_edge"] or {}

check(ap["openwrt_topology_collector_available"][1].value == 1, "ap run reports available")
check_invariants("ap", ap_nodes, ap_edges)

check(by_id(ap_nodes, "internet") == nil, "a downstream ap never fabricates an internet node")
for _, edge in ipairs(ap_edges) do
  check(not tostring(edge.raw.id):match("^wan:"), "a downstream ap never fabricates a wan edge")
end

-- Both boxes must name the gateway node identically, and the AP's copy must
-- lose to the gateway's.
local gw_placeholder = by_id(ap_nodes, "router:192.168.0.1")
check(gw_placeholder ~= nil, "ap emits a gateway node with the same id the gateway uses")
check(gw_placeholder and gw_placeholder.raw.authority == "0", "ap's gateway node is a placeholder")
check(by_id(ap_edges, "uplink:openwrt-test") ~= nil, "ap uplinks to the gateway")
check(by_id(ap_edges, "ap:openwrt-test") == nil, "ap does not emit the gateway's router-to-ap edge")

local ap_node = by_id(ap_nodes, "ap:openwrt-test")
check(ap_node and ap_node.raw.authority == "1", "an ap owns its own ap node first-hand")

-- The AP only knows about clients on its own radios. Claiming every DHCP
-- lease it can see on the bridge is what made wifi clients render as wired.
check(by_id(ap_nodes, "client:a4:83:e7:aa:bb:cc") ~= nil, "ap emits its associated clients")
local ap_client = by_id(ap_nodes, "client:a4:83:e7:aa:bb:cc")
check(ap_client and ap_client.raw.authority == "0", "ap's client nodes are placeholders")
check(by_id(ap_nodes, "client:78:8c:b5:93:fb:a9") == nil, "ap does not claim the gateway's wired clients")
for _, edge in ipairs(ap_edges) do
  check(not tostring(edge.raw.id):match("^lan:"), "ap never emits a wired edge")
end
check(by_id(ap_nodes, "port:openwrt-test:lan1") == nil, "ap does not emit a switch port tier")

-- 4. bridge fdb unavailable: no port tier, wired clients fall back. ----------
reset_mocks()
MOCK.bridge_fdb_available = false
local no_bridge = run()
local nb_nodes = no_bridge["openwrt_topology_node"] or {}
local nb_edges = no_bridge["openwrt_topology_edge"] or {}
check_invariants("no-bridge", nb_nodes, nb_edges)
check(by_id(nb_nodes, "port:openwrt-test:lan1") == nil, "no port nodes when the bridge tool is unavailable")
local fallback = by_id(nb_edges, "lan:78:8c:b5:93:fb:a9")
check(fallback and fallback.raw.source == "router:192.168.0.1",
  "wired clients attach to the router when port data is unavailable")

-- 5. Disappearing client: a client that vanishes from getHostHints (e.g. it
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
check_invariants("post-disconnect", nodes2, edges2)

-- The BSS that client was the only station on drops to zero, not to a
-- vanished node -- the radio topology is still real even with nobody on it.
local bss_after = by_id(nodes2, "bss:60:cf:84:f2:a4:32")
check(bss_after ~= nil, "bss node survives its last client disconnecting")
check(bss_after and tonumber(bss_after.value) == 0, "bss station count drops to zero, not fabricated")

-- 6. Total failure: no identity source at all reports unavailable with zero
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
  print("PASS: tests/test_topology.lua (" .. #nodes .. " nodes, " .. #edges .. " edges in the gateway run)")
  os.exit(0)
end
print(failures .. " failure(s)")
os.exit(1)
