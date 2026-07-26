-- Optional Netifyd DPI collector.
-- Reads the bounded local status snapshot; it never opens a remote API or
-- emits unbounded flow/MAC labels. Netifyd remains an optional dependency.

local json
local ok_safe, safe_json = pcall(require, "cjson.safe")
if ok_safe then
  json = safe_json
else
  local ok_json, plain_json = pcall(require, "cjson")
  if ok_json then json = plain_json end
end

local STATUS_PATH = "/var/run/netifyd/status.json"
local MAX_SERIES = 25
-- Netifyd rewrites status.json on its own timer. A file that stopped being
-- updated means the daemon died or wedged; without this bound the collector
-- would keep re-exporting a frozen snapshot as if it were live.
local MAX_STATUS_AGE = 300

local function label(value, fallback)
  value = tostring(value or fallback or "unknown")
  value = value:gsub("[^%w%._%-]", "_")
  if value == "" then return fallback or "unknown" end
  return value
end

local function number(value)
  return tonumber(value) or 0
end

local ok_nixio, nixio = pcall(require, "nixio")

local function status_age()
  -- Returns snapshot age in seconds, or nil when it cannot be determined.
  if ok_nixio and nixio.fs and nixio.fs.stat then
    local ok, stat = pcall(nixio.fs.stat, STATUS_PATH)
    if ok and stat and stat.mtime then
      return os.time() - stat.mtime
    end
  end
  return nil
end

local function read_status()
  if not json then return nil end
  local file = io.open(STATUS_PATH, "r")
  if not file then return nil end
  local raw = file:read("*a")
  file:close()
  if not raw or raw == "" then return nil end
  local ok, decoded = pcall(json.decode, raw)
  if not ok or type(decoded) ~= "table" then return nil end
  return decoded
end

local function list_from(data, name)
  if type(data[name]) == "table" then return data[name] end
  if type(data.stats) == "table" and type(data.stats[name]) == "table" then
    return data.stats[name]
  end
  return {}
end

local function value(entry, names)
  for _, name in ipairs(names) do
    if entry[name] ~= nil then return number(entry[name]) end
  end
  return 0
end

local function top_entries(entries, names)
  local values = {}
  for _, entry in ipairs(entries) do
    if type(entry) == "table" then
      local name = entry.name or entry.application or entry.protocol or entry.label
      if name then
        values[#values + 1] = {name = label(name), value = value(entry, names)}
      end
    end
  end
  -- Tie-break on name: table.sort is not stable, so ties (very common when many
  -- applications sit at 0) would otherwise reshuffle the top-N between scrapes
  -- and churn a fresh set of series every time.
  table.sort(values, function(left, right)
    if left.value ~= right.value then return left.value > right.value end
    return left.name < right.name
  end)
  return values
end

local function collect(data, applications, application_flows, protocols, devices, flows)
  local stats = data.stats or data
  devices({}, value(stats, {"devices", "device_count", "total_devices"}))
  flows({}, value(stats, {"total_flows", "flows", "active_flows"}))

  local app_entries = top_entries(list_from(data, "applications"), {"bytes", "total_bytes", "rx_bytes", "traffic"})
  local app_flow_entries = top_entries(list_from(data, "applications"), {"flows", "flow_count"})
  for index = 1, math.min(MAX_SERIES, #app_entries) do
    local entry = app_entries[index]
    applications({application = entry.name}, entry.value)
  end
  for index = 1, math.min(MAX_SERIES, #app_flow_entries) do
    local entry = app_flow_entries[index]
    application_flows({application = entry.name}, entry.value)
  end

  local protocol_entries = top_entries(list_from(data, "protocols"), {"flows", "flow_count", "count"})
  for index = 1, math.min(MAX_SERIES, #protocol_entries) do
    local entry = protocol_entries[index]
    protocols({protocol = entry.name}, entry.value)
  end
  return true
end

local function scrape()
  local available = metric("openwrt_dpi_collector_available", "gauge")
  local age_metric = metric("openwrt_dpi_status_age_seconds", "gauge")
  local applications = metric("openwrt_dpi_application_bytes", "gauge")
  local application_flows = metric("openwrt_dpi_application_flows", "gauge")
  local protocols = metric("openwrt_dpi_protocol_flows", "gauge")
  local devices = metric("openwrt_dpi_devices", "gauge")
  local flows = metric("openwrt_dpi_active_flows", "gauge")

  local data = read_status()
  if not data then
    available({}, 0)
    return
  end

  local age = status_age()
  if age then age_metric({}, age) end
  if age and age > MAX_STATUS_AGE then
    -- A readable but frozen snapshot is not a working DPI profile. Reporting 1
    -- here made a dead netifyd indistinguishable from a healthy idle one.
    available({}, 0)
    return
  end

  local ok, completed = pcall(collect, data, applications, application_flows, protocols, devices, flows)
  available({}, (ok and completed) and 1 or 0)
end

return {scrape = scrape}
