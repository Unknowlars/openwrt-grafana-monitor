-- MAC address -> hardware vendor and device icon.
--
-- Installed as /usr/lib/lua/openwrt_oui.lua, NOT into
-- /usr/lib/lua/prometheus-collectors/: the exporter turns every file in that
-- directory into a collector named after the file and calls scrape() on it.
-- This is a plain module, loaded with require() from topology.lua.
--
-- The data table is generated from the IEEE MA-L registry by
-- scripts/build_oui_table.py. Nothing here is hand-written, because a guessed OUI
-- assignment renders a confidently wrong vendor on the dashboard.
--
-- Every lookup failure returns nil rather than a fallback string: the caller
-- decides how to present "unknown", and an absent vendor is never confused
-- with a real one.

local ok_data, data = pcall(require, "openwrt_oui_data")
if not ok_data or type(data) ~= "table" or type(data.index) ~= "string" then
  data = nil
end

local M = {}

-- Present so callers can degrade explicitly instead of silently emitting
-- unlabelled nodes when the generated table failed to install.
M.available = data ~= nil

local function hex_only(mac)
  if type(mac) ~= "string" then return nil end
  local hex = mac:lower():gsub("[^0-9a-f]", "")
  if #hex ~= 12 then return nil end
  -- 00:00:00 really is registered (to Xerox), but the all-zero address is
  -- the "no attribution" placeholder that conntrack and nlbw emit. Naming it
  -- Xerox would be technically defensible and completely misleading.
  if hex == "000000000000" then return nil end
  return hex
end

-- IEEE 802 sets bit 1 of the first octet on locally administered addresses.
-- Those are not in any registry: Wi-Fi privacy randomisation, hypervisor NICs
-- and hand-assigned addresses all land here.
local function is_local(hex)
  local first = tonumber(hex:sub(1, 2), 16)
  return first ~= nil and first % 4 >= 2
end

local function vendor_at(slot)
  local vendors = data.vendors
  local name = vendors[(slot - 1) * 2 + 1]
  local icon = vendors[(slot - 1) * 2 + 2]
  return name, icon
end

-- data.index is sorted fixed-width records: prefix_len hex characters of
-- 24-bit OUI followed by a base-36 index into data.vendors.
local function search(prefix)
  local index = data.index
  local record = data.record_len
  local plen = data.prefix_len
  local low, high = 0, (#index / record) - 1
  while low <= high do
    local mid = math.floor((low + high) / 2)
    local offset = mid * record
    local key = index:sub(offset + 1, offset + plen)
    if key == prefix then
      return tonumber(index:sub(offset + plen + 1, offset + record), 36)
    elseif key < prefix then
      low = mid + 1
    else
      high = mid - 1
    end
  end
  return nil
end

local function local_range(hex)
  local ranges = data.local_ranges or {}
  for i = 1, #ranges, 3 do
    local prefix = ranges[i]
    if prefix and hex:sub(1, #prefix) == prefix then
      return ranges[i + 1], ranges[i + 2]
    end
  end
  return nil
end

-- Returns vendor, icon, is_locally_administered.
--
-- vendor/icon are nil when the address is unregistered or the prefix is not
-- in the generated table. is_locally_administered is still meaningful in that
-- case and is what lets the caller say "private address" instead of
-- "unknown vendor" -- a materially different and more useful statement.
function M.lookup(mac)
  local hex = hex_only(mac)
  if not hex then return nil, nil, false end

  local local_bit = is_local(hex)
  if not data then return nil, nil, local_bit end

  if local_bit then
    local name, icon = local_range(hex)
    return name, icon, true
  end

  local slot = search(hex:sub(1, data.prefix_len))
  if not slot then return nil, nil, false end

  local name, icon = vendor_at(slot)
  return name, icon, false
end

return M
