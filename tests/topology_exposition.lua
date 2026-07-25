-- Renders the topology collector's output as Prometheus exposition text for a
-- simulated two-router network, so the dashboard's reconciliation PromQL can be
-- checked end to end against a real engine before anything is deployed.
--
--   REPO=. lua5.1 tests/topology_exposition.lua gateway|ap
local root = os.getenv("REPO") or "."
local ROLE = arg[1]

local function load_fixture(path)
  local pipe = io.popen("python3 " .. root .. "/tests/json_to_lua.py " .. path)
  local literal = pipe:read("*a")
  pipe:close()
  return loadstring("return " .. literal)()
end

local GETHOSTHINTS = load_fixture(root .. "/tests/fixtures/gethosthints.json")

-- openwrt-main is the gateway; openwrt-ap1 is a dumb AP behind it.
local CFG
if ROLE == "gateway" then
  CFG = {
    hostname = "openwrt-main",
    lan = {up = true, device = "br-lan", ["ipv4-address"] = {{address = "192.168.0.1", mask = 24}}},
    wan = {up = true, device = "wan", ["ipv4-address"] = {{address = "192.168.2.5", mask = 24}}},
    route = "default via 192.168.2.1 dev wan proto static src 192.168.2.5\n",
    own = {"60:cf:84:f2:a4:30", "60:cf:84:f2:a4:31", "60:cf:84:f2:a4:32"},
    bssid = {["phy0-ap0"] = "60:cf:84:f2:a4:31", ["phy1-ap0"] = "60:cf:84:f2:a4:32"},
    -- The gateway cannot see AP1's association list, so 78:8c:... looks wired
    -- to it, and it holds no association of its own here.
    assoc = {["phy0-ap0"] = {}, ["phy1-ap0"] = {["f4:a3:10:5a:e9:74"] = {signal = -57}}},
    fdb = "78:8c:b5:93:fb:a9 dev lan1 master br-lan\n"
       .. "60:cf:84:f5:89:90 dev lan1 master br-lan\n"
       .. "d8:3a:dd:c0:5e:e2 dev lan2 master br-lan\n",
  }
else
  CFG = {
    hostname = "openwrt-ap1",
    lan = {up = true, device = "br-lan", ["ipv4-address"] = {{address = "192.168.0.2", mask = 24}}},
    wan = nil,
    route = "default via 192.168.0.1 dev br-lan proto static\n",
    own = {"60:cf:84:f5:89:90", "60:cf:84:f5:89:91", "60:cf:84:f5:89:92"},
    bssid = {["phy0-ap0"] = "60:cf:84:f5:89:91", ["phy1-ap0"] = "60:cf:84:f5:89:92"},
    -- 78:8c:... is really on AP1's 2.4 GHz radio, at a strong signal.
    assoc = {["phy0-ap0"] = {["78:8c:b5:93:fb:a9"] = {signal = -34}}, ["phy1-ap0"] = {}},
    fdb = "",
  }
end

-- One DHCP world, owned by the gateway. AP1's own bridge MAC is a lease like
-- any other, which is exactly why it used to render as a client.
local HOSTS = {
  ["a4:83:e7:aa:bb:cc"] = {name = "living-room-tv", ipaddrs = {"192.168.0.42"}},
  ["78:8c:b5:93:fb:a9"] = {name = "C100", ipaddrs = {"192.168.0.122"}},
  ["f4:a3:10:5a:e9:74"] = {name = nil, ipaddrs = {"192.168.0.206"}},
  ["d8:3a:dd:c0:5e:e2"] = {name = nil, ipaddrs = {"192.168.0.161"}},
  ["60:cf:84:f5:89:90"] = {name = nil, ipaddrs = {"192.168.0.2"}},
}
if ROLE ~= "gateway" then HOSTS = {} end
for mac, entry in pairs(GETHOSTHINTS) do
  if ROLE == "gateway" and mac:lower() ~= "60:cf:84:f2:a4:30" then
    local norm = mac:lower()
    if HOSTS[norm] == nil then HOSTS[norm] = entry end
  end
end

local WIRELESS = {
  radio0 = {config = {band = "2g", htmode = "HE20"},
            interfaces = {{ifname = "phy0-ap0", config = {ssid = "peach_24ghz", network = {"lan"}, mode = "ap"}}}},
  radio1 = {config = {band = "5g", htmode = "HE80"},
            interfaces = {{ifname = "phy1-ap0", config = {ssid = "peach_5ghz", network = {"lan"}, mode = "ap"}}}},
}

package.preload["ubus"] = function()
  return {connect = function()
    return {
      call = function(_, object, method, params)
        if object == "luci-rpc" and method == "getHostHints" then
          if next(HOSTS) == nil then return {["00:00:00:00:00:01"] = {name = "stub", ipaddrs = {}}} end
          return HOSTS
        end
        if object == "network.wireless" and method == "status" then return WIRELESS end
        if object == "network.interface" and method == "status" then
          if params.interface == "lan" then return CFG.lan end
          if params.interface == "wan" then return CFG.wan end
        end
        return nil
      end,
      close = function() end,
    }
  end}
end

package.preload["iwinfo"] = function()
  return {
    type = function() return "nl80211" end,
    nl80211 = {
      assoclist = function(ifname) return CFG.assoc[ifname] or {} end,
      channel = function(ifname) return ifname == "phy0-ap0" and 6 or 149 end,
    },
  }
end
package.preload["openwrt_oui_data"] = function() return dofile(root .. "/openwrt/lua/oui_data.lua") end
package.preload["openwrt_oui"] = function() return dofile(root .. "/openwrt/lua/oui.lua") end

local function reader(text)
  local pos, h = 1, {}
  function h:read()
    if pos > #text then return nil end
    local nl = text:find("\n", pos, true)
    local line
    if nl then line = text:sub(pos, nl - 1); pos = nl + 1 else line = text:sub(pos); pos = #text + 1 end
    return line
  end
  function h:lines() return function() return h:read() end end
  function h:close() end
  return h
end

local real_open, real_popen = io.open, io.popen
io.open = function(path, mode)
  if path == "/proc/sys/kernel/hostname" then return reader(CFG.hostname .. "\n") end
  if path == "/proc/net/arp" then return real_open(root .. "/tests/fixtures/proc-net-arp.txt", mode) end
  if path == "/tmp/dhcp.leases" then return real_open(root .. "/tests/fixtures/dhcp.leases", mode) end
  local ifname = path:match("^/sys/class/net/(.+)/address$")
  if ifname then
    if CFG.bssid[ifname] then return reader(CFG.bssid[ifname] .. "\n") end
    return nil
  end
  local dev = path:match("^/sys/class/net/(.+)/operstate$")
  if dev then return reader("up\n") end
  return real_open(path, mode)
end
io.popen = function(command, mode)
  if command:match("^cat /sys/class/net/") then
    return real_popen("printf '%s\\n' " .. table.concat(CFG.own, " "))
  end
  if command:match("^ip route show default") then
    return reader(CFG.route)
  end
  if command:match("^bridge fdb show") then
    return reader(CFG.fdb)
  end
  return real_popen(command, mode)
end

local out = {}
function metric(name)
  return function(labels, value)
    local parts = {}
    local keys = {}
    for k in pairs(labels or {}) do keys[#keys + 1] = k end
    table.sort(keys)
    for _, k in ipairs(keys) do
      parts[#parts + 1] = string.format('%s="%s"', k, tostring(labels[k]))
    end
    if #parts > 0 then
      out[#out + 1] = string.format("%s{%s} %s", name, table.concat(parts, ","), tostring(value))
    else
      out[#out + 1] = string.format("%s %s", name, tostring(value))
    end
  end
end

dofile(root .. "/openwrt/collectors/topology.lua").scrape()
io.open, io.popen = real_open, real_popen
print(table.concat(out, "\n"))
