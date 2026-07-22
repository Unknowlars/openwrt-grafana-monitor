-- Network topology collector: reshapes the same identity/association data
-- client_inventory.lua uses into the Grafana node-graph metric contract --
-- openwrt_topology_node / openwrt_topology_edge -- so the bundled Prometheus
-- datasource can drive a node graph panel with no plugin install. See
-- docs/client-topology-and-netflow-plan.md §2.2-§2.4 for why the contract is
-- shaped this way, and §9.7/§10.2 for the id/label rules this file follows.
--
-- This collector is deliberately self-contained rather than requiring
-- client_inventory.lua: every other collector in this directory (see
-- wifi_dethrash.lua) makes its own independent ubus/iwinfo calls, and the
-- exporter loads each collector file standalone.
--
-- Node and edge frames MUST come from the same snapshot in the same scrape --
-- a dangling edge endpoint (a source/target with no matching node id) crashes
-- Grafana's node graph panel, it does not just render incompletely (plan
-- §2.1). Every edge below is only appended after its source and target ids
-- have been added to the node id set built earlier in the same collect() run.

local ok_ubus, ubus = pcall(require, "ubus")
local ok_iwinfo, iwinfo = pcall(require, "iwinfo")

local function sanitize(value, fallback)
  if value == nil or value == "" then return fallback end
  value = tostring(value):gsub("[^%w%._%-]", "_")
  if value == "" then return fallback end
  return value
end

local function normalize_mac(value)
  if type(value) ~= "string" then return nil end
  local mac = value:lower()
  if mac:match("^%x%x:%x%x:%x%x:%x%x:%x%x:%x%x$") then return mac end
  return nil
end

local function router_hostname()
  local file = io.open("/proc/sys/kernel/hostname", "r")
  if not file then return "unknown" end
  local value = file:read("*l")
  file:close()
  return sanitize(value, "unknown")
end

-- Mirrors client_inventory.lua's router_own_macs(): getHostHints includes the
-- router's own br-lan identity as if it were a client (confirmed live, M1).
local function router_own_macs()
  local macs = {}
  local pipe = io.popen("cat /sys/class/net/*/address 2>/dev/null")
  if not pipe then return macs end
  for line in pipe:lines() do
    local mac = normalize_mac(line)
    if mac then macs[mac] = true end
  end
  pipe:close()
  return macs
end

-- "<expiry_epoch> <mac> <ip> <hostname> <clientid>", one lease per line.
local function leasefile_hosts()
  local hosts = {}
  local file = io.open("/tmp/dhcp.leases", "r")
  if not file then return hosts, false end
  for line in file:lines() do
    local _, mac, ip, name = line:match("^(%d+)%s+(%S+)%s+(%S+)%s+(%S+)")
    mac = normalize_mac(mac)
    if mac then
      if name == "*" then name = nil end
      hosts[mac] = {name = name, ip = ip and ip ~= "*" and ip or ""}
    end
  end
  file:close()
  return hosts, true
end

-- /proc/net/arp columns: IP HW-type Flags HW-address Mask Device. Flag bit 1
-- (0x2) is ATF_COM -- a resolved entry, not stale/incomplete/failed. Used as
-- the wired-liveness proxy, matching client_inventory.lua.
local function reachable_ips()
  local reachable = {}
  local file = io.open("/proc/net/arp", "r")
  if not file then return reachable end
  local first = true
  for line in file:lines() do
    if first then
      first = false
    else
      local ip, flags = line:match("^(%S+)%s+%S+%s+(%S+)")
      local flag_value = flags and tonumber(flags, 16)
      if ip and flag_value and math.floor(flag_value / 2) % 2 == 1 then
        reachable[ip] = true
      end
    end
  end
  file:close()
  return reachable
end

-- network.wireless status -> ifname -> {ssid, band, network}. Identical
-- approach to client_inventory.lua's wifi_ifaces(), confirmed live (M1) that
-- iface.config.ssid/network and radio.config.band need no UCI cross-join.
local function wifi_ifaces(connection)
  local ifaces = {}
  local ok, status = pcall(function() return connection:call("network.wireless", "status", {}) end)
  if not ok or type(status) ~= "table" then return ifaces end

  for device, radio in pairs(status) do
    local band = type(radio) == "table" and type(radio.config) == "table" and radio.config.band or nil
    for _, iface in ipairs((type(radio) == "table" and radio.interfaces) or {}) do
      local ifname = iface.ifname
      if ifname then
        local config = type(iface) == "table" and iface.config or {}
        ifaces[ifname] = {
          device = device,
          ssid = config.ssid or "",
          band = band or "",
        }
      end
    end
  end

  return ifaces
end

-- mac (lowercase) -> ifname, for every associated station. Second return
-- value tells callers whether assoclist data could be gathered at all.
local function assoc_by_mac(ifaces)
  local assoc = {}
  if not ok_iwinfo then return assoc, false end
  local any_ok = false
  for ifname in pairs(ifaces) do
    local ok_kind, kind = pcall(iwinfo.type, ifname)
    local wifi = ok_kind and kind and iwinfo[kind]
    if wifi and wifi.assoclist then
      local ok_list, stations = pcall(wifi.assoclist, ifname)
      if ok_list and type(stations) == "table" then
        any_ok = true
        for key, value in pairs(stations) do
          local mac = normalize_mac(key)
          if not mac and type(value) == "table" then mac = normalize_mac(value.mac) end
          if mac then assoc[mac] = ifname end
        end
      end
    end
  end
  return assoc, any_ok
end

-- Adds a node and records its id in `known_ids` so later edges can be
-- validated against it before being emitted.
local function add_node(node_metric, known_ids, id, labels, value)
  labels.id = id
  node_metric(labels, value)
  known_ids[id] = true
end

-- Only emits the edge if both endpoints are already known node ids -- the
-- enforcement mechanism for the "no dangling edge endpoint" acceptance
-- criterion, kept local to this file rather than trusted to caller discipline.
local function add_edge(edge_metric, known_ids, id, source, target, value, extra)
  if not (known_ids[source] and known_ids[target]) then return end
  local labels = {id = id, source = source, target = target}
  if extra then
    for key, val in pairs(extra) do labels[key] = val end
  end
  edge_metric(labels, value)
end

local function collect(node_metric, edge_metric)
  local hosts, hosts_ok = {}, false
  local ifaces = {}
  local assoc, assoc_ok = {}, false

  if ok_ubus then
    local connection = ubus.connect()
    if connection then
      local ok_hh, hh = pcall(function() return connection:call("luci-rpc", "getHostHints", {}) end)
      if ok_hh and type(hh) == "table" and next(hh) ~= nil then
        for mac, entry in pairs(hh) do
          local norm = normalize_mac(mac)
          if norm and type(entry) == "table" then
            hosts[norm] = {name = entry.name, ip = (entry.ipaddrs or {})[1] or ""}
          end
        end
        hosts_ok = true
      end

      ifaces = wifi_ifaces(connection)
      assoc, assoc_ok = assoc_by_mac(ifaces)
      connection:close()
    end
  end

  if not hosts_ok then
    hosts, hosts_ok = leasefile_hosts()
  end

  if not hosts_ok then return false end

  local own_macs = router_own_macs()
  local macs = {}
  for mac in pairs(hosts) do
    if not own_macs[mac] then macs[#macs + 1] = mac end
  end
  table.sort(macs)

  local reachable = reachable_ips()
  local ap_name = router_hostname()
  local router_id = "router:" .. ap_name
  local ap_id = "ap:" .. ap_name

  local known_ids = {}

  -- Internet, router, and AP nodes are always present: the router node is
  -- true by construction (this scrape is running on it), and on this
  -- single-exporter-per-router deployment shape (plan §2.3) the AP node
  -- shares its identity with the router node.
  add_node(node_metric, known_ids, "internet", {title = "Internet", subtitle = "WAN", icon = "cloud"}, 1)
  add_node(node_metric, known_ids, router_id, {title = ap_name, subtitle = "OpenWrt", icon = "sitemap", arc__ok = "1", arc__warn = "0"}, 1)
  add_node(node_metric, known_ids, ap_id, {title = ap_name, subtitle = "Access Point", icon = "wifi", arc__ok = "1", arc__warn = "0"}, 1)

  add_edge(edge_metric, known_ids, "wan:" .. ap_name, "internet", router_id, 1)
  add_edge(edge_metric, known_ids, "ap:" .. ap_name, router_id, ap_id, 1)

  -- SSID nodes: one per distinct ssid@band pair currently configured, keyed
  -- so two radios broadcasting the same SSID name (2.4G + 5G) get distinct
  -- nodes -- see plan §9.7 for the id shape.
  local ssid_ids = {}
  local ssid_station_counts = {}
  for _, iface in pairs(ifaces) do
    if iface.ssid ~= "" then
      local ssid_id = "ssid:" .. iface.ssid .. "@" .. iface.band
      if not ssid_ids[ssid_id] then
        ssid_ids[ssid_id] = {ssid = iface.ssid, band = iface.band}
        ssid_station_counts[ssid_id] = 0
      end
    end
  end
  for mac, ifname in pairs(assoc) do
    local iface = ifaces[ifname]
    if iface and iface.ssid ~= "" then
      local ssid_id = "ssid:" .. iface.ssid .. "@" .. iface.band
      ssid_station_counts[ssid_id] = (ssid_station_counts[ssid_id] or 0) + 1
    end
  end
  for ssid_id, info in pairs(ssid_ids) do
    add_node(node_metric, known_ids, ssid_id, {title = info.ssid, subtitle = info.band, icon = "signal", detail__band = info.band}, ssid_station_counts[ssid_id] or 0)
    add_edge(edge_metric, known_ids, "radio:" .. info.ssid .. "@" .. info.band, ap_id, ssid_id, ssid_station_counts[ssid_id] or 0)
  end

  -- Client nodes and their assoc/lan edges. Connection type mirrors
  -- client_inventory.lua exactly: "unknown" (assoc data unavailable at all)
  -- deliberately produces no edge rather than guessing wired -- an isolated
  -- node is honest, a wrong-shaped edge is the "plausible wrong value" the
  -- plan's standing rules forbid.
  for _, mac in ipairs(macs) do
    local host = hosts[mac]
    local hostname = sanitize(host.name, nil) or ("unknown_" .. mac:gsub(":", ""))
    local ip = host.ip or ""
    local client_id = "client:" .. mac
    local ifname = assoc[mac]

    local is_up
    if ifname then
      is_up = 1
    elseif ip ~= "" and reachable[ip] then
      is_up = 1
    else
      is_up = 0
    end

    add_node(node_metric, known_ids, client_id, {
      title = hostname,
      subtitle = ip,
      icon = "laptop",
      detail__mac = mac,
      detail__ip = ip,
      arc__online = is_up == 1 and "1" or "0",
      arc__offline = is_up == 1 and "0" or "1",
    }, is_up)

    if ifname then
      local iface = ifaces[ifname]
      if iface and iface.ssid ~= "" then
        local ssid_id = "ssid:" .. iface.ssid .. "@" .. iface.band
        add_edge(edge_metric, known_ids, "assoc:" .. mac, ssid_id, client_id, 1)
      end
    elseif assoc_ok then
      -- assoc data was gathered successfully and this mac simply is not in
      -- it, so it is wired, not unknown.
      add_edge(edge_metric, known_ids, "lan:" .. mac, router_id, client_id, 1)
    end
  end

  return true
end

local function scrape()
  local available = metric("openwrt_topology_collector_available", "gauge")
  local node_metric = metric("openwrt_topology_node", "gauge")
  local edge_metric = metric("openwrt_topology_edge", "gauge")

  -- Availability is reported only after collection completes -- see plan
  -- §9.1 and client_inventory.lua. Every external read happens before any
  -- metric() call above is invoked.
  local ok, completed = pcall(collect, node_metric, edge_metric)
  available({}, (ok and completed) and 1 or 0)
end

return {scrape = scrape}
