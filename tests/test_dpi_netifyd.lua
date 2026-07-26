-- Executes the real dpi_netifyd collector against a Netifyd-shaped snapshot.
--
-- Covers the three behaviours that made the DPI tile untrustworthy on the live
-- router, where the collector reported available=1 with zero devices, zero
-- flows and no application series at all.

local root = (arg[0]:match("^(.*)/tests/") or ".")

local function decode(raw)
  local tmp = os.tmpname()
  local handle = io.open(tmp, "w")
  handle:write(raw)
  handle:close()
  local pipe = io.popen("python3 " .. root .. "/tests/json_to_lua.py " .. tmp)
  local literal = pipe:read("*a")
  pipe:close()
  os.remove(tmp)
  local chunk = loadstring("return " .. literal)
  if not chunk then return nil end
  return chunk()
end

package.preload["cjson.safe"] = function() return {decode = decode} end

local samples
function metric(name)
  return function(labels, value)
    local parts = {}
    for key, val in pairs(labels or {}) do
      parts[#parts + 1] = key .. '="' .. tostring(val) .. '"'
    end
    table.sort(parts)
    samples[#samples + 1] = {name = name, labels = "{" .. table.concat(parts, ",") .. "}", value = value}
  end
end

local failures = 0
local function check(condition, message)
  if not condition then
    print("FAIL: " .. message)
    failures = failures + 1
  end
end

-- Run the collector with a controllable snapshot age.
local function run(age_seconds)
  samples = {}
  package.loaded["nixio"] = nil
  package.preload["nixio"] = function()
    return {fs = {stat = function() return {mtime = os.time() - age_seconds} end}}
  end

  local real_open = io.open
  io.open = function(path, mode)
    if path == "/var/run/netifyd/status.json" then
      return real_open(root .. "/tests/fixtures/netifyd-status.json", mode)
    end
    return real_open(path, mode)
  end

  local collector = dofile(root .. "/openwrt/collectors/dpi_netifyd.lua")
  collector.scrape()
  io.open = real_open

  local index = {}
  for _, sample in ipairs(samples) do
    index[sample.name] = index[sample.name] or {}
    table.insert(index[sample.name], sample)
  end
  return index
end

-- Fresh snapshot -------------------------------------------------------------
local fresh = run(10)
check(fresh["openwrt_dpi_collector_available"][1].value == 1, "fresh snapshot reports available")
check(fresh["openwrt_dpi_devices"][1].value == 4, "device count read from snapshot")
check(fresh["openwrt_dpi_active_flows"][1].value == 137, "flow count read from snapshot")
check(#fresh["openwrt_dpi_application_bytes"] == 3, "one series per application")

-- Deterministic ordering: equal values must break ties by name, so the exported
-- top-N set does not churn between scrapes.
local second = run(10)
local function names(index)
  local out = {}
  for _, sample in ipairs(index["openwrt_dpi_application_bytes"] or {}) do
    out[#out + 1] = sample.labels
  end
  table.sort(out)
  return table.concat(out, ",")
end
check(names(fresh) == names(second), "application series set is deterministic across scrapes")

-- Stale snapshot -------------------------------------------------------------
local stale = run(3600)
check(stale["openwrt_dpi_collector_available"][1].value == 0,
  "a snapshot older than the freshness bound must report unavailable")
check(stale["openwrt_dpi_status_age_seconds"] ~= nil, "snapshot age is exported for troubleshooting")
check(stale["openwrt_dpi_application_bytes"] == nil,
  "no application series exported from a frozen snapshot")

if failures == 0 then
  print("PASS: tests/test_dpi_netifyd.lua")
  os.exit(0)
end
print(failures .. " failure(s)")
os.exit(1)
