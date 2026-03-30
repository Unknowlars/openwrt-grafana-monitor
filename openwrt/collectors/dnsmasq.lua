local ubus = require "ubus"

local function scrape_leasefile(path, lease_metric)
  local file = io.open(path, "r")
  if not file then
    return
  end

  for line in file:lines() do
    local expires, mac, ip, hostname = string.match(line, "^(%S+)%s+(%S+)%s+(%S+)%s+(%S+)")
    if expires and ip and hostname then
      local labels = {
        dnsmasq = path,
        ip = ip,
        hostname = hostname,
      }
      if mac and mac ~= "*" then
        labels.mac = string.upper(mac)
      end
      lease_metric(labels, tonumber(expires))
    end
  end

  file:close()
end

local function scrape()
  local lease_metric = metric("dhcp_lease", "gauge")
  local seen_leasefile = {}

  local conn = ubus.connect()
  if conn then
    local metrics = conn:call("dnsmasq", "metrics", {})
    if metrics then
      for name, value in pairs(metrics) do
        metric("dnsmasq_" .. name, "counter", nil, value)
      end
    end

    local values = conn:call("uci", "get", {config = "dhcp", type = "dnsmasq"})
    if values then
      for _, configs in pairs(values) do
        for _, config in pairs(configs) do
          local leasefile = config.leasefile
          if leasefile and not seen_leasefile[leasefile] then
            seen_leasefile[leasefile] = true
            scrape_leasefile(leasefile, lease_metric)
          end
        end
      end
    end

    conn:close()
  end

  if not seen_leasefile["/tmp/dhcp.leases"] then
    scrape_leasefile("/tmp/dhcp.leases", lease_metric)
  end
end

return { scrape = scrape }
