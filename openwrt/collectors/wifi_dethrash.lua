-- Optional WiFi mesh collector adapted from the openwrt-dethrash signal model.
-- It is intentionally isolated from the core profile because station/MAC series
-- can be high-cardinality and usteer is not installed on every router.

local ok_ubus, ubus = pcall(require, "ubus")
local ok_iwinfo, iwinfo = pcall(require, "iwinfo")
local ok_nixio, nixio = pcall(require, "nixio")
local ok_uci, uci = pcall(require, "uci")

local function hostname()
  local file = io.open("/proc/sys/kernel/hostname", "r")
  if not file then return "unknown" end
  local value = file:read("*l") or "unknown"
  file:close()
  return value:gsub("[^%w%._%-]", "_")
end

local function resolve_ip(ip)
  if not ok_nixio then return ip end
  local ok, name = pcall(nixio.getnameinfo, ip)
  if ok and name then return name:match("^([%w%-]+)") or ip end
  return ip
end

local function scrape()
  local available = metric("openwrt_wifi_mesh_collector_available", "gauge")
  if not ok_ubus or not ok_iwinfo then
    available({}, 0)
    return
  end
  available({}, 1)

  local txpower = metric("wifi_radio_txpower_dbm", "gauge")
  local txpower_offset = metric("wifi_radio_txpower_offset_dbm", "gauge")
  local channel = metric("wifi_radio_channel", "gauge")
  local frequency = metric("wifi_radio_frequency_mhz", "gauge")
  local ieee80211r = metric("wifi_iface_ieee80211r_enabled", "gauge")
  local ieee80211k = metric("wifi_iface_ieee80211k_enabled", "gauge")
  local ieee80211v = metric("wifi_iface_ieee80211v_enabled", "gauge")

  local connection = ubus.connect()
  if not connection then return end
  local status = connection:call("network.wireless", "status", {}) or {}
  local iface_by_device = {}

  for device, radio in pairs(status) do
    iface_by_device[device] = {}
    for _, iface in ipairs(radio.interfaces or {}) do
      local ifname = iface.ifname
      if ifname then
        local ok_kind, kind = pcall(iwinfo.type, ifname)
        local wifi = kind and iwinfo[kind]
        if wifi then
          local ok_ssid, ssid = pcall(wifi.ssid, ifname)
          if not ok_ssid then ssid = "" end
          local labels = {device = device, ifname = ifname, ssid = ssid}
          table.insert(iface_by_device[device], {ifname = ifname, ssid = ssid})
          local ok_value, value = pcall(wifi.txpower, ifname)
          if not ok_value then value = nil end
          if value then txpower(labels, value) end
          if wifi.txpower_offset then
            ok_value, value = pcall(wifi.txpower_offset, ifname)
            if not ok_value then value = nil end
          else
            value = nil
          end
          if value then txpower_offset(labels, value) end
          ok_value, value = pcall(wifi.channel, ifname)
          if not ok_value then value = nil end
          if value then channel(labels, value) end
          ok_value, value = pcall(wifi.frequency, ifname)
          if not ok_value then value = nil end
          if value then frequency(labels, value) end
        end
      end
    end
  end

  if ok_uci then
    local cursor = uci.cursor()
    cursor:foreach("wireless", "wifi-iface", function(section)
      local device = section.device or ""
      local ssid = section.ssid or ""
      local ifname = ""
      for _, iface in ipairs(iface_by_device[device] or {}) do
        if iface.ssid == ssid then ifname = iface.ifname break end
      end
      local labels = {device = device, ifname = ifname, ssid = ssid}
      ieee80211r(labels, section.ieee80211r == "1" and 1 or 0)
      ieee80211k(labels, section.ieee80211k == "1" and 1 or 0)
      ieee80211v(labels, (section.ieee80211v == "1" or section.bss_transition == "1") and 1 or 0)
    end)
  end

  local ok_usteer, usteer = pcall(function() return connection:call("usteer", "local_info", {}) end)
  if ok_usteer and usteer then
    local roam_source = metric("wifi_usteer_roam_events_source", "gauge")
    local roam_target = metric("wifi_usteer_roam_events_target", "gauge")
    local load = metric("wifi_usteer_load", "gauge")
    local associated = metric("wifi_usteer_associated_clients", "gauge")
    local ap = hostname()
    for node, details in pairs(usteer) do
      local name = ap .. "/" .. (details.ssid or node)
      local labels = {ap = name}
      roam_source(labels, (details.roam_events or {}).source or 0)
      roam_target(labels, (details.roam_events or {}).target or 0)
      load(labels, details.load or 0)
      associated(labels, details.n_assoc or 0)
    end
  end
  connection:close()
end

return {scrape = scrape}
