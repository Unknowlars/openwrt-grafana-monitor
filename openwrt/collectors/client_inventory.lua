-- Unified client inventory collector.
--
-- Identity spine is ubus `luci-rpc getHostHints`, which already merges the
-- neighbour table, /etc/ethers, the DHCP leasefile, reverse DNS, getifaddrs(),
-- and UCI static leases (in that priority order) into one MAC-keyed map. This
-- collector adds the two things getHostHints does not know: which radio/SSID
-- a client is associated with, and whether it is associated at all. See
-- docs/client-topology-and-netflow-plan.md §1.1 for the full design.
--
-- mac is the primary key everywhere in this file: lowercase, colon-separated,
-- validated with a MAC-shaped pattern rather than run through the generic
-- label sanitizer (which would eat the colons) -- see plan §9.7.

local ok_ubus, ubus = pcall(require, "ubus")
local ok_iwinfo, iwinfo = pcall(require, "iwinfo")
local ok_uci, uci = pcall(require, "uci")

local DEFAULT_MAX = 256
local SEEN_FILE = "/etc/openwrt-client-seen"

local function config_value(name, fallback)
  local file = io.open("/etc/openwrt-grafana-monitor.conf", "r")
  if not file then return fallback end
  for line in file:lines() do
    local value = line:match("^" .. name .. "=(.*)$")
    if value then
      value = value:gsub('^"', ""):gsub('"$', "")
      file:close()
      return value
    end
  end
  file:close()
  return fallback
end

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

-- Bit 1 (the locally-administered bit) of the first octet. Replaces an OUI
-- vendor lookup, which mostly fails on modern per-SSID randomised MACs
-- anyway -- see plan §6/§8.
local function is_locally_administered(mac)
  local first_octet = tonumber(mac:sub(1, 2), 16)
  if not first_octet then return false end
  return math.floor(first_octet / 2) % 2 == 1
end

local function router_hostname()
  local file = io.open("/proc/sys/kernel/hostname", "r")
  if not file then return "unknown" end
  local value = file:read("*l")
  file:close()
  return sanitize(value, "unknown")
end

-- getHostHints includes the router's own interfaces (confirmed live: br-lan's
-- own address came back as a "client" named after the router's own hostname,
-- with the router's own LAN IP). A client inventory should not list the
-- router as one of its own clients, so every local interface's own MAC is
-- excluded from the emitted set.
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
local function lease_expiry_by_mac()
  local expiry = {}
  local file = io.open("/tmp/dhcp.leases", "r")
  if not file then return expiry end
  for line in file:lines() do
    local exp, mac = line:match("^(%d+)%s+(%S+)")
    mac = normalize_mac(mac)
    if mac and exp then expiry[mac] = tonumber(exp) end
  end
  file:close()
  return expiry
end

-- Fallback identity source when getHostHints/rpcd-mod-luci is unavailable.
-- This cannot supply ap/ssid/band -- those come only from the ubus wireless
-- calls below -- so a leasefile-only run leaves those labels empty rather
-- than guessing.
local function leasefile_hosts()
  local hosts = {}
  local file = io.open("/tmp/dhcp.leases", "r")
  if not file then return hosts, false end
  for line in file:lines() do
    local _, mac, ip, name = line:match("^(%d+)%s+(%S+)%s+(%S+)%s+(%S+)")
    mac = normalize_mac(mac)
    if mac then
      if name == "*" then name = nil end
      hosts[mac] = {name = name, ipaddrs = ip and ip ~= "*" and {ip} or {}, ip6addrs = {}}
    end
  end
  file:close()
  return hosts, true
end

-- /proc/net/arp columns: IP HW-type Flags HW-address Mask Device. Flag bit 1
-- (0x2) is ATF_COM -- a resolved entry, not stale/incomplete/failed. This is
-- the "neighbour table" liveness proxy for wired clients; WiFi clients get a
-- better signal from the assoclist below.
local function arp_by_ip()
  local reachable = {}
  local file = io.open("/proc/net/arp", "r")
  if not file then return reachable end
  local first = true
  for line in file:lines() do
    if first then
      first = false
    else
      local ip, flags, device = line:match("^(%S+)%s+%S+%s+(%S+)%s+%S+%s+%S+%s+(%S+)")
      local flag_value = flags and tonumber(flags, 16)
      if ip and flag_value and math.floor(flag_value / 2) % 2 == 1 then
        reachable[ip] = device or true
      end
    end
  end
  file:close()
  return reachable
end

-- WiFi carries its logical network directly in network.wireless status. For
-- wired clients, resolve the ARP device against UCI network.interface device
-- / ifname fields. If either source is unavailable, use explicit "unknown"
-- rather than assigning a plausible-but-wrong trusted label.
local function network_by_device()
  local networks = {}
  if not ok_uci then return networks end
  pcall(function()
    local cursor = uci.cursor()
    cursor:foreach("network", "interface", function(section)
      local name = section[".name"]
      local devices = section.device or section.ifname or ""
      for device in tostring(devices):gmatch("[^%s]+") do
        networks[device] = name
      end
    end)
  end)
  return networks
end

local function static_leases()
  local statics = {}
  if not ok_uci then return statics end
  pcall(function()
    local cursor = uci.cursor()
    cursor:foreach("dhcp", "host", function(section)
      local mac = normalize_mac(section.mac)
      if mac then statics[mac] = true end
    end)
  end)
  return statics
end

-- uci get firewall.@defaults[0].flow_offloading{,_hw}. Read defensively: a
-- misread here must not make the rest of the collector look broken.
local function flow_offload_state()
  local state = {}
  if not ok_uci then return state end
  pcall(function()
    local cursor = uci.cursor()
    cursor:foreach("firewall", "defaults", function(section)
      state.sw = section.flow_offloading
      state.hw = section.flow_offloading_hw
    end)
  end)
  return state
end

-- network.wireless status: for every radio interface, resolve
-- ifname -> {ssid, band, network}. Confirmed against a live router
-- (2026-07-22, OpenWrt 24.10-class ubus) rather than assumed: `ssid` and
-- `network` live directly on iface.config, and `band` ("2g"/"5g"/"6g") lives
-- directly on the radio's own config -- no iwinfo call and no separate UCI
-- wifi-iface cross-join needed for any of this. The first draft of this
-- function did both, guessing at a shape that turned out to be unnecessarily
-- fragile; this replaced it after live verification, not before.
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
        local network = type(config.network) == "table" and config.network[1] or nil
        ifaces[ifname] = {
          device = device,
          ssid = config.ssid or "",
          band = band or "",
          network = network,
        }
      end
    end
  end

  return ifaces
end

-- mac (lowercase) -> ifname, for every associated station across every known
-- wifi interface. Returns a second value: whether assoclist data could be
-- gathered at all, so callers can tell "no wifi clients" from "we don't know".
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

local function load_seen_store()
  local store = {}
  local file = io.open(SEEN_FILE, "r")
  if not file then return store end
  for line in file:lines() do
    local mac, first, last = line:match("^(%S+)%s+(%d+)%s+(%d+)$")
    mac = normalize_mac(mac)
    if mac then store[mac] = {first = tonumber(first), last = tonumber(last)} end
  end
  file:close()
  return store
end

local function save_seen_store(store)
  -- Staged in the same directory as the target so the final os.rename is a
  -- same-filesystem, atomic rename. /etc is not the same filesystem as /tmp
  -- on OpenWrt (unlike /var, see the helper-script convention in plan §9.2),
  -- so staging under /tmp here would risk a cross-device rename failure.
  local tmp = SEEN_FILE .. ".tmp." .. tostring(os.time())
  local file = io.open(tmp, "w")
  if not file then return false end
  for mac, record in pairs(store) do
    file:write(mac .. " " .. record.first .. " " .. record.last .. "\n")
  end
  file:close()
  return os.rename(tmp, SEEN_FILE) and true or false
end

-- Touches every currently-seen mac (updating or creating its record), evicts
-- least-recently-seen entries when the store exceeds max_entries, persists
-- the result, and returns the (possibly trimmed) store so callers can read
-- first_seen for each mac just touched.
local function update_seen_store(seen_macs, max_entries, now)
  local store = load_seen_store()
  for _, mac in ipairs(seen_macs) do
    if store[mac] then
      store[mac].last = now
    else
      store[mac] = {first = now, last = now}
    end
  end

  local count = 0
  for _ in pairs(store) do count = count + 1 end
  if count > max_entries then
    local ordered = {}
    for mac, record in pairs(store) do ordered[#ordered + 1] = {mac = mac, last = record.last} end
    table.sort(ordered, function(a, b)
      if a.last ~= b.last then return a.last < b.last end
      return a.mac < b.mac
    end)
    for index = 1, count - max_entries do
      store[ordered[index].mac] = nil
    end
  end

  save_seen_store(store)
  return store
end

local function collect(info, up, lease_expiry_metric, ipv6_metric, first_seen_metric, offload_metric, truncated_metric)
  local max_clients = tonumber(config_value("CLIENT_INVENTORY_MAX", "")) or DEFAULT_MAX

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
            hosts[norm] = {
              name = entry.name,
              ipaddrs = entry.ipaddrs or {},
              ip6addrs = entry.ip6addrs or {},
            }
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

  local truncated = #macs > max_clients
  local emit_macs = {}
  for index = 1, math.min(#macs, max_clients) do emit_macs[index] = macs[index] end

  local ap = router_hostname()
  local expiry = lease_expiry_by_mac()
  local statics = static_leases()
  local arp = arp_by_ip()
  local networks = network_by_device()
  local offload = flow_offload_state()

  local ok_seen, seen_store = pcall(update_seen_store, emit_macs, max_clients, os.time())
  if not ok_seen then
    seen_store = {}
    for _, mac in ipairs(emit_macs) do seen_store[mac] = {first = os.time()} end
  end

  for _, mac in ipairs(emit_macs) do
    local host = hosts[mac]
    local hostname = sanitize(host.name, nil) or ("unknown_" .. mac:gsub(":", ""))
    local ip = host.ipaddrs[1] or ""
    local ifname = assoc[mac] or ""
    local wifi_iface = ifname ~= "" and ifaces[ifname] or nil
    local ssid = (wifi_iface and wifi_iface.ssid) or ""
    local band = (wifi_iface and wifi_iface.band) or ""
    local network = (wifi_iface and wifi_iface.network and wifi_iface.network ~= "") and wifi_iface.network
      or networks[arp[ip]] or "unknown"

    local conn
    if not assoc_ok then
      conn = "unknown"
    elseif assoc[mac] then
      conn = "wifi"
    else
      conn = "wired"
    end

    local labels = {
      mac = mac,
      hostname = hostname,
      ip = ip,
      ap = ap,
      ssid = ssid,
      band = band,
      ifname = ifname,
      connection = conn,
      network = network,
      static = statics[mac] and "1" or "0",
      mac_type = is_locally_administered(mac) and "local" or "global",
      has_ipv6 = (#(host.ip6addrs or {}) > 0) and "1" or "0",
    }
    info(labels, 1)

    local is_up = 0
    if conn == "wifi" then
      is_up = 1
    elseif ip ~= "" and arp[ip] then
      is_up = 1
    end
    up({mac = mac}, is_up)

    local exp = expiry[mac]
    if exp then
      lease_expiry_metric({mac = mac}, exp)
    elseif statics[mac] then
      -- Matches the existing dhcp_lease convention: 0 means static/infinite,
      -- so it does not render as an impossible negative "remaining" duration.
      lease_expiry_metric({mac = mac}, 0)
    end

    ipv6_metric({mac = mac}, #(host.ip6addrs or {}))

    local record = seen_store[mac]
    first_seen_metric({mac = mac}, record and record.first or os.time())
  end

  if offload.sw ~= nil then offload_metric({mode = "sw"}, offload.sw == "1" and 1 or 0) end
  if offload.hw ~= nil then offload_metric({mode = "hw"}, offload.hw == "1" and 1 or 0) end

  truncated_metric({}, truncated and 1 or 0)

  return true
end

local function scrape()
  local available = metric("openwrt_client_inventory_collector_available", "gauge")
  local info = metric("openwrt_client_info", "gauge")
  local up = metric("openwrt_client_up", "gauge")
  local lease_expiry = metric("openwrt_client_lease_expiry_seconds", "gauge")
  local ipv6_addresses = metric("openwrt_client_ipv6_addresses", "gauge")
  local first_seen = metric("openwrt_client_first_seen_seconds", "gauge")
  local offload = metric("openwrt_flow_offload_enabled", "gauge")
  local truncated = metric("openwrt_client_inventory_truncated", "gauge")

  -- Availability is reported only after collection completes -- see plan
  -- §9.1. Every external read (ubus, iwinfo, uci, leasefile, arp, seen-store)
  -- happens before any metric() call above is invoked, so a failure at any
  -- point during gathering (ubus dying mid-run, a malformed response, a
  -- read-only /etc) leaves zero partial series, not a half-populated scrape.
  local ok, completed = pcall(collect, info, up, lease_expiry, ipv6_addresses, first_seen, offload, truncated)
  available({}, (ok and completed) and 1 or 0)
end

return {scrape = scrape}
