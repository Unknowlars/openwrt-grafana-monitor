local function scrape()
  local data = space_split(get_contents("/tmp/packetloss.out"))
  if data[1] ~= nil then
    metric("packet_loss", "gauge", nil, tonumber(data[1]))
  end
end

return { scrape = scrape }
