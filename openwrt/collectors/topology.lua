-- Network topology collector: reshapes the identity/association data
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
--
-- MULTI-ROUTER SHAPE
--
-- Several exporters scrape into one Grafana panel, so node ids must be stable
-- across routers and each fact must have exactly one first-hand owner.
--
--   * A box is a `gateway` if it has a WAN interface of its own, otherwise it
--     is a downstream `ap` whose default route points at the gateway. Only a
--     gateway emits `internet`, `modem:`, `router:` and `port:` nodes; a
--     fabricated second uplink is worse than no uplink.
--   * The gateway node id is `router:<lan-ip>`, not `router:<hostname>`: an AP
--     knows its gateway's IP from the default route but can never know its
--     hostname, and both must name the node identically.
--   * BSS nodes are keyed by BSSID, which is globally unique. Two APs
--     broadcasting the same SSID used to collide on one `ssid:<name>@<band>`
--     id and overwrite each other's station counts.
--   * Every node and edge carries `authority`: "1" when this box observed the
--     fact first-hand, "0" when it is a placeholder emitted only so an edge
--     endpoint resolves. The dashboard prefers authority-1 and falls back to
--     authority-0, so a client that only the gateway can name keeps its name
--     while the AP that it is actually associated to still gets to draw the
--     association. Emitting the placeholder is what keeps the panel safe when
--     the other exporter is down.
--   * `openwrt_topology_infra_mac` lists this box's own interface MACs so the
--     dashboard can drop routers that appear as each other's DHCP clients.
--
-- Signal strength rides on the association *edge* (as its colour), not on the
-- client node: the gateway owns the node because it owns the identity, and
-- link quality is a property of the link anyway.

local ok_ubus, ubus = pcall(require, "ubus")
local ok_iwinfo, iwinfo = pcall(require, "iwinfo")
local ok_oui, oui = pcall(require, "openwrt_oui")
if not ok_oui or type(oui) ~= "table" or type(oui.lookup) ~= "function" then
  oui = nil
end

-- Strict: used for anything that becomes part of a node/edge id, where a
-- separator or quote would corrupt the graph.
local function sanitize(value, fallback)
  if value == nil or value == "" then return fallback end
  value = tostring(value):gsub("[^%w%._%-]", "_")
  if value == "" then return fallback end
  return value
end

-- Relaxed: used for human-facing titles and detail rows. Spaces read far
-- better than underscores in "Raspberry Pi", and Prometheus label values
-- accept them; only quote, backslash and newline would need escaping, and
-- none of them survive this pattern.
local function sanitize_text(value, fallback)
  if value == nil or value == "" then return fallback end
  value = tostring(value):gsub("[^%w%._%-%s:!/]", " "):gsub("%s+", " "):gsub("^%s+", ""):gsub("%s+$", "")
  if value == "" then return fallback end
  return value
end

local function normalize_mac(value)
  if type(value) ~= "string" then return nil end
  local mac = value:lower()
  if mac:match("^%x%x:%x%x:%x%x:%x%x:%x%x:%x%x$") then return mac end
  return nil
end

local function read_line(path)
  local file = io.open(path, "r")
  if not file then return nil end
  local value = file:read("*l")
  file:close()
  return value
end

local function router_hostname()
  return sanitize(read_line("/proc/sys/kernel/hostname"), "unknown")
end

-- RFC1918 plus CGNAT. A private WAN nexthop means the ISP box is doing its own
-- NAT, which is worth showing as a distinct hop rather than pretending the
-- router faces the internet directly.
local function is_private_ipv4(ip)
  if type(ip) ~= "string" then return false end
  local a, b = ip:match("^(%d+)%.(%d+)%.")
  a, b = tonumber(a), tonumber(b)
  if not a or not b then return false end
  if a == 10 then return true end
  if a == 192 and b == 168 then return true end
  if a == 172 and b >= 16 and b <= 31 then return true end
  if a == 100 and b >= 64 and b <= 127 then return true end
  return false
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
-- the wired-liveness proxy, matching client_inventory.lua. The MAC and Device
-- columns are kept as well: the MAC names the upstream modem, and the Device
-- column is the fallback when `bridge fdb` is unavailable.
local function arp_table()
  local reachable, by_ip = {}, {}
  local file = io.open("/proc/net/arp", "r")
  if not file then return reachable, by_ip end
  local first = true
  for line in file:lines() do
    if first then
      first = false
    else
      local ip, flags, mac, device = line:match("^(%S+)%s+%S+%s+(%S+)%s+(%S+)%s+%S+%s+(%S+)")
      local flag_value = flags and tonumber(flags, 16)
      if ip and flag_value then
        local resolved = math.floor(flag_value / 2) % 2 == 1
        if resolved then reachable[ip] = true end
        by_ip[ip] = {mac = normalize_mac(mac), device = device, reachable = resolved}
      end
    end
  end
  file:close()
  return reachable, by_ip
end

local function popen_lines(command)
  local pipe = io.popen(command)
  if not pipe then return {} end
  local lines = {}
  for line in pipe:lines() do lines[#lines + 1] = line end
  pipe:close()
  return lines
end

-- "default via 192.168.0.1 dev br-lan proto static" -- nexthop and egress
-- device. A default route with no `via` (a point-to-point WAN) still yields a
-- device, which is all the role check needs.
local function default_route()
  for _, line in ipairs(popen_lines("ip route show default 2>/dev/null")) do
    local via, device = line:match("^default%s+via%s+(%S+)%s+dev%s+(%S+)")
    if not via then
      device = line:match("^default%s+dev%s+(%S+)")
    end
    if device then return via, device end
  end
  return nil, nil
end

local function network_status(connection, interface)
  local ok, status = pcall(function()
    return connection:call("network.interface", "status", {interface = interface})
  end)
  if ok and type(status) == "table" then return status end
  return nil
end

local function first_ipv4(status)
  if type(status) ~= "table" then return nil end
  local list = status["ipv4-address"]
  if type(list) == "table" and type(list[1]) == "table" then return list[1].address end
  return nil
end

-- Gateway vs downstream AP. A box is only a gateway if it has a WAN interface
-- of its own that is not the LAN bridge; everything else is an AP hanging off
-- someone else's gateway.
local function detect_role(connection)
  local route_via, route_device = default_route()
  local lan_status = connection and network_status(connection, "lan") or nil
  local lan_device = type(lan_status) == "table" and lan_status.device or nil
  local lan_ip = first_ipv4(lan_status)

  local wan_ip, wan_device
  if connection then
    for _, name in ipairs({"wan", "wwan"}) do
      local status = network_status(connection, name)
      if type(status) == "table" and status.up and status.device then
        wan_device = status.device
        wan_ip = first_ipv4(status)
        break
      end
    end
  end

  local is_gateway
  if wan_device and wan_device ~= lan_device then
    is_gateway = true
  elseif route_device and lan_device and route_device == lan_device then
    is_gateway = false
  elseif route_device and lan_device and route_device ~= lan_device then
    is_gateway = true
  else
    -- No usable evidence either way. Assume gateway only when there is no
    -- default-route nexthop to point at instead -- the standalone case.
    is_gateway = route_via == nil
  end

  return {
    gateway = is_gateway,
    -- On a gateway this is our own LAN address; on an AP it is the nexthop.
    -- Both name the same node.
    gateway_ip = is_gateway and lan_ip or route_via,
    wan_gateway = is_gateway and route_via or nil,
    wan_ip = wan_ip,
    lan_device = lan_device,
  }
end

-- network.wireless status -> ifname -> radio/interface facts. Identical
-- approach to client_inventory.lua's wifi_ifaces(), confirmed live (M1) that
-- iface.config.ssid/network and radio.config.band need no UCI cross-join.
-- Only `ap` mode interfaces become BSS nodes; a mesh or station vif is a
-- different kind of link and is better absent than mislabelled as an AP.
local function wifi_ifaces(connection)
  local ifaces = {}
  local ok, status = pcall(function() return connection:call("network.wireless", "status", {}) end)
  if not ok or type(status) ~= "table" then return ifaces end

  for device, radio in pairs(status) do
    local radio_config = type(radio) == "table" and radio.config or {}
    local band = type(radio_config) == "table" and radio_config.band or nil
    local htmode = type(radio_config) == "table" and radio_config.htmode or nil
    for _, iface in ipairs((type(radio) == "table" and radio.interfaces) or {}) do
      local ifname = iface.ifname
      local config = type(iface) == "table" and iface.config or {}
      if ifname and (config.mode == nil or config.mode == "ap") then
        local networks = type(config.network) == "table" and config.network or {}
        ifaces[ifname] = {
          device = device,
          -- Sanitized at the single source so the bss node ids, edge ids, and
          -- node titles built from it downstream all agree, and so the value
          -- matches the one client_inventory.lua and
          -- openwrt-monitor-client-conntrack.sh emit for the same SSID.
          ssid = sanitize(config.ssid, ""),
          band = sanitize(band, ""),
          htmode = sanitize(htmode, ""),
          network = sanitize(networks[1], ""),
          -- The vif's own MAC is its BSSID; reading it from sysfs avoids
          -- depending on iwinfo.bssid() being present in this build.
          bssid = normalize_mac(read_line("/sys/class/net/" .. ifname .. "/address")),
        }
      end
    end
  end

  return ifaces
end

-- Fills in channel from iwinfo where available. Absent channel just means a
-- shorter subtitle, never a wrong one.
local function annotate_channels(ifaces)
  if not ok_iwinfo then return end
  for ifname, iface in pairs(ifaces) do
    local ok_kind, kind = pcall(iwinfo.type, ifname)
    local wifi = ok_kind and kind and iwinfo[kind]
    if wifi then
      if not iface.bssid and wifi.bssid then
        local ok_bssid, bssid = pcall(wifi.bssid, ifname)
        if ok_bssid then iface.bssid = normalize_mac(bssid) end
      end
      if wifi.channel then
        local ok_channel, channel = pcall(wifi.channel, ifname)
        if ok_channel and tonumber(channel) then iface.channel = tonumber(channel) end
      end
    end
  end
end

-- mac (lowercase) -> {ifname, signal}, for every associated station. Second
-- return value tells callers whether assoclist data could be gathered at all.
-- The station table iwinfo already returns carries signal; keeping it costs
-- nothing and is what colours the association edges.
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
          if mac then
            assoc[mac] = {
              ifname = ifname,
              signal = type(value) == "table" and tonumber(value.signal) or nil,
            }
          end
        end
      end
    end
  end
  return assoc, any_ok
end

-- `bridge fdb show` maps a learned MAC to the bridge port behind which it
-- lives, which is the only way to place a wired client on the right switch
-- port. Requires the `bridge` tool; when it is missing the port tier is
-- skipped entirely and wired clients attach straight to the router, rather
-- than being drawn on a guessed port.
local function bridge_fdb(wifi_ifnames)
  local by_mac, ports, any = {}, {}, false
  for _, line in ipairs(popen_lines("bridge fdb show 2>/dev/null")) do
    local mac, port, bridge = line:match("^(%S+)%s+dev%s+(%S+)%s+master%s+(%S+)")
    mac = normalize_mac(mac)
    -- `permanent` entries are the bridge's own addresses, not learned clients.
    if mac and port and bridge and not line:match("permanent") and not wifi_ifnames[port] then
      local first = tonumber(mac:sub(1, 2), 16)
      -- Skip multicast/broadcast group addresses (low bit of the first octet).
      if first and first % 2 == 0 then
        any = true
        by_mac[mac] = port
        ports[port] = true
      end
    end
  end
  return by_mac, ports, any
end

local function link_up(device)
  local value = read_line("/sys/class/net/" .. device .. "/operstate")
  return value == "up"
end

local function signal_color(signal)
  if not signal then return nil end
  if signal >= -60 then return "green" end
  if signal >= -72 then return "orange" end
  return "red"
end

-- Adds a node and records its id in `known_ids` so later edges can be
-- validated against it before being emitted.
local function add_node(node_metric, known_ids, id, labels, value, authority)
  labels.id = id
  labels.authority = authority and "1" or "0"
  node_metric(labels, value)
  known_ids[id] = true
end

-- Only emits the edge if both endpoints are already known node ids -- the
-- enforcement mechanism for the "no dangling edge endpoint" acceptance
-- criterion, kept local to this file rather than trusted to caller discipline.
local function add_edge(edge_metric, known_ids, id, source, target, value, authority, extra)
  if not (known_ids[source] and known_ids[target]) then return end
  local labels = {id = id, source = source, target = target, authority = authority and "1" or "0"}
  if extra then
    for key, val in pairs(extra) do labels[key] = val end
  end
  edge_metric(labels, value)
end

-- Title/icon/vendor for a client, in descending order of how much it tells a
-- human. A locally administered address is reported as private rather than
-- unknown: that is a materially different and more useful statement, and it
-- is the one thing that can be said with certainty about an unregistered MAC.
local function describe_client(mac, hostname)
  local vendor, icon, is_local
  if oui then vendor, icon, is_local = oui.lookup(mac) end

  local tail = mac:sub(10)
  local title = sanitize_text(hostname, nil)
  if title then
    title = title:gsub("%.lan$", "")
  elseif vendor then
    title = sanitize_text(vendor .. " " .. tail, nil)
  elseif is_local then
    title = "Private " .. tail
  else
    title = mac
  end

  return title, vendor, icon or "laptop", is_local
end

local function collect(node_metric, edge_metric, infra_metric)
  local hosts, hosts_ok = {}, false
  local ifaces = {}
  local assoc, assoc_ok = {}, false
  local role = {gateway = true}

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

      role = detect_role(connection)
      ifaces = wifi_ifaces(connection)
      annotate_channels(ifaces)
      assoc, assoc_ok = assoc_by_mac(ifaces)
      connection:close()
    end
  end

  if not hosts_ok then
    hosts, hosts_ok = leasefile_hosts()
  end

  if not hosts_ok then return false end

  local own_macs = router_own_macs()
  for mac in pairs(own_macs) do
    infra_metric({mac = mac}, 1)
  end

  local reachable, arp_by_ip = arp_table()
  local ap_name = router_hostname()
  local router_id = role.gateway_ip and ("router:" .. sanitize(role.gateway_ip, "unknown")) or ("router:" .. ap_name)
  local ap_id = "ap:" .. ap_name

  local known_ids = {}

  -- ── Uplink spine ───────────────────────────────────────────────────────
  --
  -- The gateway owns it. A downstream AP emits only an authority-0 stand-in
  -- for the gateway node so its uplink edge resolves even when the gateway's
  -- own exporter is unreachable.
  if role.gateway then
    add_node(node_metric, known_ids, "internet",
      {title = "Internet", subtitle = "WAN", icon = "cloud", arc__ok = "1", arc__warn = "0"}, 1, true)

    add_node(node_metric, known_ids, router_id, {
      title = ap_name,
      subtitle = sanitize_text("Gateway " .. (role.gateway_ip or ""), "Gateway"),
      icon = "sitemap",
      arc__ok = "1",
      arc__warn = "0",
      detail__role = "gateway",
      detail__lan_ip = role.gateway_ip or "",
      detail__wan_ip = role.wan_ip or "",
    }, 1, true)

    -- A private WAN nexthop is the ISP's own router doing a second layer of
    -- NAT. It used to surface as an ordinary LAN client because it sits in
    -- the ARP table like one; naming it is both more correct and more useful.
    local modem_ip = role.wan_gateway
    if modem_ip and is_private_ipv4(modem_ip) then
      local modem_id = "modem:" .. sanitize(modem_ip, "upstream")
      local entry = arp_by_ip[modem_ip]
      local vendor
      if entry and entry.mac and oui then vendor = (oui.lookup(entry.mac)) end
      -- The upstream router answers ARP on the WAN interface, so getHostHints
      -- lists it exactly like a LAN client and it used to be drawn twice: once
      -- as this modem node and once as a laptop hanging off the gateway.
      if entry and entry.mac then own_macs[entry.mac] = true end
      add_node(node_metric, known_ids, modem_id, {
        title = sanitize_text(vendor, "Upstream router"),
        subtitle = sanitize_text(modem_ip, ""),
        icon = "cloud",
        arc__ok = "1",
        arc__warn = "0",
        detail__ip = modem_ip,
        detail__mac = entry and entry.mac or "",
        detail__vendor = sanitize_text(vendor, ""),
        detail__note = "double NAT: the WAN nexthop is a private address",
      }, 1, true)
      add_edge(edge_metric, known_ids, "wan:" .. sanitize(modem_ip, "upstream"), "internet", modem_id, 1, true)
      add_edge(edge_metric, known_ids, "nat:" .. ap_name, modem_id, router_id, 1, true)
    else
      add_edge(edge_metric, known_ids, "wan:" .. ap_name, "internet", router_id, 1, true)
    end
  else
    add_node(node_metric, known_ids, router_id, {
      title = sanitize_text(role.gateway_ip, "Gateway"),
      subtitle = "Gateway",
      icon = "sitemap",
      arc__ok = "1",
      arc__warn = "0",
      detail__role = "gateway",
      detail__note = "placeholder emitted by a downstream AP",
    }, 1, false)
  end

  -- ── This box as an access point ────────────────────────────────────────
  local has_wifi = next(ifaces) ~= nil
  if has_wifi then
    add_node(node_metric, known_ids, ap_id, {
      title = ap_name,
      subtitle = role.gateway and "Access Point" or "Access Point (mesh AP)",
      icon = "wifi",
      arc__ok = "1",
      arc__warn = "0",
      detail__role = role.gateway and "gateway+ap" or "ap",
    }, 1, true)

    if role.gateway then
      add_edge(edge_metric, known_ids, "ap:" .. ap_name, router_id, ap_id, 1, true)
    else
      add_edge(edge_metric, known_ids, "uplink:" .. ap_name, router_id, ap_id, 1, true)
    end
  end

  -- ── BSS nodes, one per radio interface ─────────────────────────────────
  --
  -- Keyed by BSSID rather than ssid@band: two APs broadcasting one SSID are
  -- two distinct basic service sets on two distinct channels, and merging
  -- them into a single node made their station counts overwrite each other.
  local wifi_ifnames = {}
  local bss_counts, bss_of_ifname = {}, {}
  for ifname, iface in pairs(ifaces) do
    wifi_ifnames[ifname] = true
    if iface.bssid and iface.ssid ~= "" then
      local bss_id = "bss:" .. iface.bssid
      bss_of_ifname[ifname] = bss_id
      bss_counts[bss_id] = 0
    end
  end
  for _, entry in pairs(assoc) do
    local bss_id = bss_of_ifname[entry.ifname]
    if bss_id then bss_counts[bss_id] = (bss_counts[bss_id] or 0) + 1 end
  end

  for ifname, iface in pairs(ifaces) do
    local bss_id = bss_of_ifname[ifname]
    if bss_id and known_ids[ap_id] then
      local channel = iface.channel and ("ch" .. iface.channel) or ""
      add_node(node_metric, known_ids, bss_id, {
        title = sanitize_text(iface.ssid, "SSID"),
        subtitle = sanitize_text(iface.band .. " " .. channel, iface.band),
        icon = "signal",
        arc__ok = "1",
        arc__warn = "0",
        detail__ssid = iface.ssid,
        detail__bssid = iface.bssid,
        detail__band = iface.band,
        detail__channel = iface.channel and tostring(iface.channel) or "",
        detail__htmode = iface.htmode,
        detail__ifname = ifname,
        detail__network = iface.network,
        detail__ap = ap_name,
      }, bss_counts[bss_id] or 0, true)
      add_edge(edge_metric, known_ids, "radio:" .. iface.bssid, ap_id, bss_id, bss_counts[bss_id] or 0, true)
    end
  end

  -- ── Wired switch ports (gateway only) ──────────────────────────────────
  local fdb_by_mac, fdb_ports, fdb_ok = {}, {}, false
  if role.gateway then
    fdb_by_mac, fdb_ports, fdb_ok = bridge_fdb(wifi_ifnames)
    if fdb_ok then
      local port_names = {}
      for port in pairs(fdb_ports) do port_names[#port_names + 1] = port end
      table.sort(port_names)
      for _, port in ipairs(port_names) do
        local up = link_up(port)
        local port_id = "port:" .. ap_name .. ":" .. sanitize(port, "port")
        add_node(node_metric, known_ids, port_id, {
          title = sanitize_text(port, "port"),
          subtitle = "Switch port",
          icon = "layer-group",
          arc__ok = up and "1" or "0",
          arc__warn = up and "0" or "1",
          detail__device = port,
          detail__bridge = role.lan_device or "",
        }, 1, true)
        add_edge(edge_metric, known_ids, "link:" .. ap_name .. ":" .. sanitize(port, "port"),
          router_id, port_id, 1, true)
      end
    end
  end

  -- ── Clients ────────────────────────────────────────────────────────────
  --
  -- The gateway is the identity authority: it runs DHCP, so it is the only
  -- box that can put a hostname and an address on a node. An AP emits only
  -- the clients associated to its own radios, as authority-0 placeholders, so
  -- that its association edges have somewhere to land.
  local macs = {}
  if role.gateway then
    for mac in pairs(hosts) do
      if not own_macs[mac] then macs[#macs + 1] = mac end
    end
  else
    for mac in pairs(assoc) do
      if not own_macs[mac] then macs[#macs + 1] = mac end
    end
  end
  table.sort(macs)

  for _, mac in ipairs(macs) do
    local host = hosts[mac] or {}
    local ip = host.ip or ""
    local client_id = "client:" .. mac
    local entry = assoc[mac]
    local title, vendor, icon, is_local = describe_client(mac, host.name)

    local is_up
    if entry then
      is_up = 1
    elseif ip ~= "" and reachable[ip] then
      is_up = 1
    else
      is_up = 0
    end

    local iface = entry and ifaces[entry.ifname] or nil
    local connection_kind
    if entry then
      connection_kind = "wifi"
    elseif assoc_ok and role.gateway then
      connection_kind = "wired"
    else
      connection_kind = "unknown"
    end

    add_node(node_metric, known_ids, client_id, {
      title = title,
      subtitle = sanitize_text(ip ~= "" and ip or vendor, ""),
      icon = icon,
      arc__online = is_up == 1 and "1" or "0",
      arc__offline = is_up == 1 and "0" or "1",
      detail__mac = mac,
      detail__ip = ip,
      detail__vendor = sanitize_text(vendor, is_local and "private address" or "unknown"),
      detail__connection = connection_kind,
      detail__ssid = iface and iface.ssid or "",
      detail__band = iface and iface.band or "",
      detail__signal_dbm = entry and entry.signal and tostring(entry.signal) or "",
      detail__ifname = entry and entry.ifname or "",
      detail__port = fdb_by_mac[mac] or "",
      detail__mac_type = is_local and "local" or "global",
    }, is_up, role.gateway)

    if entry then
      local bss_id = bss_of_ifname[entry.ifname]
      if bss_id then
        local extra = {}
        local color = signal_color(entry.signal)
        if color then extra.color = color end
        add_edge(edge_metric, known_ids, "assoc:" .. mac, bss_id, client_id, 1, true, extra)
      end
    elseif assoc_ok and role.gateway then
      -- assoc data was gathered successfully and this mac simply is not on
      -- one of our radios, so from here it is wired -- it may still be on
      -- another AP's radio, which is why the dashboard drops this edge when
      -- some AP claims the same MAC.
      local port = fdb_by_mac[mac]
      local parent = port and ("port:" .. ap_name .. ":" .. sanitize(port, "port")) or router_id
      add_edge(edge_metric, known_ids, "lan:" .. mac, parent, client_id, 1, true)
    end
  end

  return true
end

local function scrape()
  local available = metric("openwrt_topology_collector_available", "gauge")
  local node_metric = metric("openwrt_topology_node", "gauge")
  local edge_metric = metric("openwrt_topology_edge", "gauge")
  local infra_metric = metric("openwrt_topology_infra_mac", "gauge")

  -- Availability is reported only after collection completes -- see plan
  -- §9.1 and client_inventory.lua. Every external read happens before any
  -- metric() call above is invoked.
  local ok, completed = pcall(collect, node_metric, edge_metric, infra_metric)
  available({}, (ok and completed) and 1 or 0)
end

return {scrape = scrape}
