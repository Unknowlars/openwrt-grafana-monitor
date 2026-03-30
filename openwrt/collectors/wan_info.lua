local function scrape()
  local labels = {}

  for field in string.gmatch(get_contents("/tmp/wanip.out"), "%S+") do
    local key, value = string.match(field, "([^=]+)=(.*)")
    if key and value then
      labels[key] = value
    end
  end

  if next(labels) ~= nil then
    metric("wan_info", "gauge", labels, 1)
  end
end

return { scrape = scrape }
