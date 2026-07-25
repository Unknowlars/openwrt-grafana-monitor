-- Executes the real device_traffic collector against real `nft -j` output.
--
-- The live router reported openwrt_device_traffic_collector_available=1 while
-- node_scrape_collector_success{collector="device_traffic"}=0 and exported no
-- traffic series at all. This harness reproduces that by feeding the collector
-- the element shape nft actually emits for a dynamic set with counters.

local root = (arg[0]:match("^(.*)/tests/") or ".")

-- Minimal JSON decoder stub: the fixture is parsed with a real parser via the
-- python side, so here we only need cjson-compatible behaviour. We shell out to
-- python3 to decode, keeping this harness dependency-free on OpenWrt-like Lua.
local function decode(raw)
  local tmp = os.tmpname()
  local handle = io.open(tmp, "w")
  handle:write(raw)
  handle:close()
  local pipe = io.popen("python3 " .. root .. "/tests/json_to_lua.py " .. tmp)
  local lua_literal = pipe:read("*a")
  pipe:close()
  os.remove(tmp)
  local chunk = loadstring("return " .. lua_literal)
  if not chunk then return nil end
  return chunk()
end

package.preload["cjson.safe"] = function()
  return {decode = decode}
end

-- Capture every emitted sample so we can assert on names, labels and duplicates.
local samples = {}
function metric(name, kind)
  return function(labels, value)
    local parts = {}
    for key, val in pairs(labels or {}) do
      parts[#parts + 1] = key .. '="' .. tostring(val) .. '"'
    end
    table.sort(parts)
    samples[#samples + 1] = {
      name = name,
      labels = "{" .. table.concat(parts, ",") .. "}",
      value = value,
    }
  end
end

-- Stub the router-side inputs the collector reads.
local real_popen = io.popen
io.popen = function(command, mode)
  if command:match("nft %-j list set inet fw4 openwrt_device_upload") then
    return real_popen("cat " .. root .. "/tests/fixtures/nft-set-upload.json", mode)
  end
  if command:match("nft %-j list set") then
    return real_popen("printf ''", mode)
  end
  return real_popen(command, mode)
end

local real_open = io.open
io.open = function(path, mode)
  if path == "/tmp/dhcp.leases" then
    return real_open(root .. "/tests/fixtures/dhcp.leases", mode)
  end
  if path == "/etc/openwrt-grafana-monitor.conf" then
    return nil
  end
  return real_open(path, mode)
end

local collector = dofile(root .. "/openwrt/collectors/device_traffic.lua")
collector.scrape()

io.popen = real_popen
io.open = real_open

-- Assertions -----------------------------------------------------------------
local failures = 0
local function check(condition, message)
  if not condition then
    print("FAIL: " .. message)
    failures = failures + 1
  end
end

local by_name, seen = {}, {}
for _, sample in ipairs(samples) do
  by_name[sample.name] = by_name[sample.name] or {}
  table.insert(by_name[sample.name], sample)
  local key = sample.name .. sample.labels
  check(not seen[key], "duplicate series emitted: " .. key)
  seen[key] = true
end

local avail = by_name["openwrt_device_traffic_collector_available"]
check(avail and #avail == 1, "availability metric emitted exactly once")
check(avail and avail[1].value == 1, "availability must be 1 when the set parsed successfully")

local bytes = by_name["openwrt_device_traffic_bytes_total"]
check(bytes and #bytes == 3, "expected 3 byte series, got " .. tostring(bytes and #bytes or 0))

-- Hostnames must come from the lease file; unknown IPs fall back to a bounded label.
local found = {}
for _, sample in ipairs(bytes or {}) do
  found[sample.labels] = sample.value
end
check(found['{device="C100",direction="upload"}'] == 402913,
  "lease hostname label with correct counter bytes")
check(found['{device="ip_192_168_0_154",direction="upload"}'] == 88213771,
  "IP fallback label for a lease-less address")

local info = by_name["openwrt_device_info"]
check(info and #info == 3, "one identity series per device")

if failures == 0 then
  print("PASS: tests/test_device_traffic.lua (" .. #samples .. " samples)")
  os.exit(0)
end
print(failures .. " failure(s)")
os.exit(1)
