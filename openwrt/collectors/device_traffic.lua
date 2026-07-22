-- Optional nftables per-device traffic collector.
-- Counters are read from the sets installed by the traffic profile.

local json
local ok_safe, safe_json = pcall(require, "cjson.safe")
if ok_safe then
  json = safe_json
else
  local ok_json, plain_json = pcall(require, "cjson")
  if ok_json then json = plain_json end
end

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

local function label(value, fallback)
  value = tostring(value or fallback or "unknown")
  value = value:gsub("[^%w%._%-]", "_")
  if value == "" then return fallback or "unknown" end
  return value
end

local function read_set(set_name)
  if not json then return nil, false end
  local command = "nft -j list set inet fw4 " .. set_name .. " 2>/dev/null"
  local pipe = io.popen(command, "r")
  if not pipe then return nil, false end
  local raw = pipe:read("*a")
  local closed = pipe:close()
  if not closed or raw == "" then return nil, false end
  local ok, decoded = pcall(json.decode, raw)
  if not ok or not decoded then return nil, false end

  for _, item in ipairs(decoded.nftables or {}) do
    if item.set and item.set.name == set_name then
      return item.set.elem or {}, true
    end
  end
  return {}, true
end

local function lease_map()
  local leases = {}
  local file = io.open("/tmp/dhcp.leases", "r")
  if not file then return leases end
  for line in file:lines() do
    local _, mac, ip, hostname = line:match("^(%S+)%s+(%S+)%s+(%S+)%s+(%S+)")
    if ip then
      if hostname == "*" then hostname = nil end
      leases[ip] = {mac = mac or "", hostname = hostname or ""}
    end
  end
  file:close()
  return leases
end

-- `nft -j list set` wraps every element of a dynamic set with counters as
--   {"elem": {"val": "192.168.0.10", "timeout": N, "counter": {...}}}
-- so the address lives at entry.elem.val. The previous version stopped at
-- entry.elem, handed a table to the label builder and threw
-- "attempt to call method 'gsub' (a nil value)", which aborted the collector
-- after it had already reported itself available.
local function entry_value(entry)
  if type(entry) ~= "table" then return nil, nil end

  -- Plain sets without counters expose the element directly as a string.
  if type(entry.elem) == "string" then
    return entry.elem, entry.counter
  end

  local node = type(entry.elem) == "table" and entry.elem or entry
  local counter = node.counter or entry.counter
  local value = node.val
  if value == nil then value = node.addr end

  if type(value) == "table" then
    value = value.addr
      or value.address
      or (type(value.prefix) == "table" and (value.prefix.addr or value.prefix.address))
      or nil
  end

  if type(value) ~= "string" then return nil, nil end
  return value, counter
end

local function collect(info, bytes, packets)
  local upload, upload_ok = read_set("openwrt_device_upload")
  local download, download_ok = read_set("openwrt_device_download")
  if not upload_ok and not download_ok then
    return false
  end

  local leases = lease_map()
  local interfaces = config_value("TRAFFIC_LAN_INTERFACE", "br-lan")
  local used_names = {}
  local emitted_info = {}

  local function emit(entries, direction)
    for _, entry in ipairs(entries or {}) do
      local ip, counter = entry_value(entry)
      if ip and counter then
        local lease = leases[ip] or {}
        local name = lease.hostname
        if not name or name == "" then name = "ip_" .. ip:gsub("%.", "_") end
        name = label(name, "unknown")
        if used_names[name] and used_names[name] ~= ip then
          name = name .. "_" .. ip:gsub("%.", "_")
        end
        used_names[name] = ip
        local mac = lease.mac or ""
        local labels = {device = name, ip = ip, mac = mac, interface = interfaces}
        if not emitted_info[ip] then
          info(labels, 1)
          emitted_info[ip] = true
        end
        bytes({device = name, direction = direction}, tonumber(counter.bytes) or 0)
        packets({device = name, direction = direction}, tonumber(counter.packets) or 0)
      end
    end
  end

  emit(upload, "upload")
  emit(download, "download")
  return true
end

local function scrape()
  local available = metric("openwrt_device_traffic_collector_available", "gauge")
  local info = metric("openwrt_device_info", "gauge")
  local bytes = metric("openwrt_device_traffic_bytes_total", "counter")
  local packets = metric("openwrt_device_traffic_packets_total", "counter")

  if not json then
    available({}, 0)
    return
  end

  -- Availability is reported only after the collection actually finished.
  -- Claiming 1 up front made a mid-collection failure look healthy on the
  -- dashboard while no traffic series were exported at all. pcall also keeps a
  -- malformed nft payload from failing this collector's whole scrape.
  local ok, completed = pcall(collect, info, bytes, packets)
  available({}, (ok and completed) and 1 or 0)
end

return {scrape = scrape}
