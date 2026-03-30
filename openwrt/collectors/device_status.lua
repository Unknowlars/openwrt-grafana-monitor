local function scrape()
  local metric_up = metric("router_device_up", "gauge")
  local metric_status = metric("router_device_status", "gauge")

  local file = io.open("/tmp/device-status.out", "r")
  if not file then
    return
  end

  for line in file:lines() do
    local labels = {}
    local up = nil

    for field in string.gmatch(line, "%S+") do
      local key, value = string.match(field, "([^=]+)=(.*)")
      if key and value then
        if key == "up" then
          up = tonumber(value)
        else
          labels[key] = value
        end
      end
    end

    if up == nil then
      if labels.status == "online" then
        up = 1
      elseif labels.status == "offline" then
        up = 0
      end
    end

    if up ~= nil then
      metric_up(labels, up)
      metric_status(labels, up)
    end
  end

  file:close()
end

return { scrape = scrape }
