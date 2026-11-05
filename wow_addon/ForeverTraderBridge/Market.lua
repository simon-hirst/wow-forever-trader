-- Market: records the whole auction house while it is open.
--
-- Two sources, neither of which needs a click:
--   * every Sniper round lists each item's lowest price and total quantity. These
--     become 5-minute bars per item, including how many units left and arrived.
--   * every full listing (C_AuctionHouse.ReplicateItems, at most one per 15 minutes
--     account-wide) gives each auction's quantity, buyout and time-left band.
-- Everything goes over the link, and into SavedVariables as the fallback.
local _, NS = ...
local Market = {}
NS.Market = Market

local floor, format, concat = math.floor, string.format, table.concat

local BAR_SECONDS = 300
local MAX_BAR_BUCKETS = 48
local MAX_SNAPSHOTS = 2
local READ_PER_FRAME = 1500
local PACK_ITEMS_PER_FRAME = 40
local FULL_LISTING_GAP = 905
local FIRST_REQUEST_DELAY = 90
local REQUEST_RETRY = 180 -- the server ignores a request inside its 15 minutes, so ask again
local SETTLE_SECONDS = 2

Market.rounds = 0
Market.lastListingTs = 0
Market.lastListingRows = 0

-- 5-minute bars ------------------------------------------------------------

local roundPrice, roundQty = {}, {}
local lastSeen = {}   -- itemID -> {price, qty}
local bar = {}        -- itemID -> {open, high, low, close, qty, out, in}
local lastClosed = {} -- itemID -> {close, qty}
local barBucket

-- Bars chunk: varint bucket, then 8 varints per row, packed as tightly as fits.
local function pushBars(bucket, rows)
    local put, limit = NS.Link.PutVarint, NS.Link.MAX_PAYLOAD
    local chunk, row = {}, {}
    put(chunk, bucket)
    local start = #chunk
    for _, values in ipairs(rows) do
        wipe(row)
        for _, v in ipairs(values) do put(row, v) end
        if #chunk + #row > limit then
            NS.Link.Enqueue(NS.Link.TYPE.BARS, NS.Link.BytesToString(chunk))
            chunk = {}
            put(chunk, bucket)
        end
        for i = 1, #row do chunk[#chunk + 1] = row[i] end
    end
    if #chunk > start then
        NS.Link.Enqueue(NS.Link.TYPE.BARS, NS.Link.BytesToString(chunk))
    end
end

function Market.CloseBar()
    if not barBucket then return end
    local rows, text = {}, {}
    for id, b in pairs(bar) do
        local prev = lastClosed[id]
        if not prev or b[6] > 0 or b[7] > 0 or b[2] ~= b[3] or prev[1] ~= b[4] or prev[2] ~= b[5] then
            rows[#rows + 1] = {id, b[1], b[2], b[3], b[4], b[5], b[6], b[7]}
            text[#text + 1] = format('%d,%.0f,%.0f,%.0f,%.0f,%d,%d,%d', id, b[1], b[2], b[3], b[4], b[5], b[6], b[7])
        end
        if prev then
            prev[1], prev[2] = b[4], b[5]
        else
            lastClosed[id] = {b[4], b[5]}
        end
    end
    wipe(bar)
    if #rows > 0 then
        pushBars(barBucket, rows)
        local db = ForeverTraderBridgeDB
        if type(db) == 'table' then
            db.bars = db.bars or {}
            db.bars[#db.bars + 1] = {t = barBucket, d = concat(text, ';')}
            while #db.bars > MAX_BAR_BUCKETS do table.remove(db.bars, 1) end
        end
    end
    barBucket = nil
end

function Market.OnBrowse(results)
    if type(results) ~= 'table' then return end
    wipe(roundPrice)
    wipe(roundQty)
    local seen = 0
    for i = 1, #results do
        local r = results[i]
        local key = r.itemKey
        local price, qty = r.minPrice or 0, r.totalQuantity or 0
        if key and price > 0 and qty > 0 then
            local id = key.itemID
            local low = roundPrice[id]
            if not low or price < low then roundPrice[id] = price end
            roundQty[id] = (roundQty[id] or 0) + qty
            seen = seen + 1
        end
    end
    if seen == 0 then return end
    Market.rounds = Market.rounds + 1
    local now = NS.Now()
    local bucket = now - now % BAR_SECONDS
    if barBucket and bucket ~= barBucket then Market.CloseBar() end
    barBucket = bucket
    for id, price in pairs(roundPrice) do
        local qty = roundQty[id]
        local b = bar[id]
        if not b then
            b = {price, price, price, price, qty, 0, 0}
            bar[id] = b
        else
            if price > b[2] then b[2] = price end
            if price < b[3] then b[3] = price end
            b[4], b[5] = price, qty
        end
        local s = lastSeen[id]
        if s then
            local change = qty - s[2]
            if change < 0 then
                b[6] = b[6] - change
            elseif change > 0 then
                b[7] = b[7] + change
            end
            s[1], s[2] = price, qty
        else
            lastSeen[id] = {price, qty}
        end
    end
    -- An item missing from a complete list has no auctions left.
    for id, s in pairs(lastSeen) do
        if not roundPrice[id] then
            local b = bar[id]
            if not b then
                b = {s[1], s[1], s[1], s[1], 0, 0, 0}
                bar[id] = b
            end
            b[5] = 0
            b[6] = b[6] + s[2]
            lastSeen[id] = nil
        end
    end
    NS.Link.MarkState()
end

-- Full listings ------------------------------------------------------------

local reader -- {phase, count, index, byItem, ids, ...} while one is being read
local pendingSince, pendingCount

function Market.OnReplicateUpdate()
    if reader then return end
    pendingSince = GetTime()
    pendingCount = C_AuctionHouse.GetNumReplicateItems() or 0
end

local function startReading(count)
    reader = {phase = 'read', count = count, index = 0, byItem = {}, ids = {}, rows = 0, ts = NS.Now()}
end

local function readSlice()
    local get, timeLeft = C_AuctionHouse.GetReplicateItemInfo, C_AuctionHouse.GetReplicateItemTimeLeft
    local stop = math.min(reader.index + READ_PER_FRAME, reader.count)
    local byItem, ids = reader.byItem, reader.ids
    for index = reader.index, stop - 1 do
        local _, _, stack, _, _, _, _, _, _, buyout, _, _, _, _, _, _, itemID = get(index)
        buyout = tonumber(buyout) or 0
        -- Bid-only auctions have no price that can be bought now.
        if itemID and itemID > 0 and stack and stack > 0 and buyout > 0 then
            local rows = byItem[itemID]
            if not rows then
                rows = {}
                byItem[itemID] = rows
                ids[#ids + 1] = itemID
            end
            local n = #rows
            rows[n + 1], rows[n + 2], rows[n + 3] = stack, buyout, timeLeft(index) or 0
            reader.rows = reader.rows + 1
        end
    end
    reader.index = stop
    if stop >= reader.count then
        reader.phase = 'pack'
        reader.packAt = 1
        reader.text = {}
        reader.chunks = {}
        reader.bytes = {}
        reader.chunkOpen = false
    end
end

-- L3 chunk: varint ts, varint chunk index, then groups of
-- [varint item][varint count*8+band][varint buyout]...[0]. Each chunk stands alone.
local function flushChunk()
    local r = reader
    if #r.bytes == 0 then return end
    r.chunks[#r.chunks + 1] = NS.Link.BytesToString(r.bytes)
    r.bytes = {}
end

local function openChunk()
    local r = reader
    NS.Link.PutVarint(r.bytes, r.ts)
    NS.Link.PutVarint(r.bytes, #r.chunks)
end

local function packSlice()
    local r = reader
    local limit = NS.Link.MAX_PAYLOAD
    local put = NS.Link.PutVarint
    local stop = math.min(r.packAt + PACK_ITEMS_PER_FRAME - 1, #r.ids)
    for i = r.packAt, stop do
        local id = r.ids[i]
        local rows = r.byItem[id]
        local parts = {}
        local groupOpen = false
        for k = 1, #rows, 3 do
            local count, buyout, band = rows[k], rows[k + 1], rows[k + 2]
            parts[#parts + 1] = format('%d.%.0f.%d', count, buyout, band)
            -- Worst case: 20 bytes for this row, 1 for the group terminator.
            if #r.bytes + 21 + 10 > limit then
                if groupOpen then r.bytes[#r.bytes + 1] = 0 end
                flushChunk()
                groupOpen = false
            end
            if #r.bytes == 0 then openChunk() end
            if not groupOpen then
                put(r.bytes, id)
                groupOpen = true
            end
            put(r.bytes, count * 8 + (band % 8))
            put(r.bytes, buyout)
        end
        if groupOpen then r.bytes[#r.bytes + 1] = 0 end
        r.text[#r.text + 1] = id .. '|' .. concat(parts, ',')
    end
    r.packAt = stop + 1
    if r.packAt > #r.ids then
        flushChunk()
        for _, chunk in ipairs(r.chunks) do
            NS.Link.Enqueue(NS.Link.TYPE.L3, chunk)
        end
        local tail = {}
        NS.Link.PutVarint(tail, r.ts)
        NS.Link.PutVarint(tail, #r.chunks)
        NS.Link.PutVarint(tail, r.rows)
        NS.Link.Enqueue(NS.Link.TYPE.L3_END, NS.Link.BytesToString(tail))
        local db = ForeverTraderBridgeDB
        if type(db) == 'table' then
            db.snapshots = db.snapshots or {}
            db.snapshots[#db.snapshots + 1] = {ts = r.ts, rows = r.rows, data = concat(r.text, ';')}
            while #db.snapshots > MAX_SNAPSHOTS do table.remove(db.snapshots, 1) end
            db.last_listing = r.ts
        end
        Market.lastListingTs, Market.lastListingRows = r.ts, r.rows
        NS.Log(format('full listing read: %d auctions, %d items, %d link frames', r.rows, #r.ids, #r.chunks))
        NS.Link.MarkState()
        reader = nil
    end
end

-- When the last full listing actually arrived: ours, or AHledger's finished scan. aux's
-- replicate_time is left out on purpose: aux sets it when it asks, even when the server
-- refuses, and live that pushed the next scan back by a further 15 minutes.
local function lastListingAny()
    local last = 0
    local db = ForeverTraderBridgeDB
    if type(db) == 'table' and (db.last_listing or 0) > last then last = db.last_listing end
    local ledger = _G.AHledgerDB
    if type(ledger) == 'table' and (ledger.lastScanAt or 0) > last then last = ledger.lastScanAt end
    return last
end

-- Seconds until the next full listing request.
function Market.NextListingIn()
    local due = lastListingAny() + FULL_LISTING_GAP
    if Market.requestedAt then due = math.max(due, Market.requestedAt + REQUEST_RETRY) end
    return math.max(0, due - NS.Now())
end

function Market.SessionStart()
    Market.sessionAt = GetTime()
end

function Market.SessionEnd()
    Market.CloseBar()
    wipe(lastSeen)
    reader, pendingSince = nil, nil
end

function Market.Tick()
    if pendingSince and not reader then
        local count = C_AuctionHouse.GetNumReplicateItems() or 0
        if count ~= pendingCount then
            pendingCount, pendingSince = count, GetTime()
        elseif GetTime() - pendingSince >= SETTLE_SECONDS then
            pendingSince = nil
            if count > 0 then startReading(count) end
        end
    end
    if reader then
        if reader.phase == 'read' then
            readSlice()
        else
            packSlice()
        end
    end
    if not NS.AuctionHouseOpen or not NS.Settings().auto then return end
    -- AHledger usually scans when the auction house opens, so give it time to go first.
    if Market.sessionAt and GetTime() - Market.sessionAt >= FIRST_REQUEST_DELAY and not reader and not pendingSince then
        if Market.NextListingIn() <= 0 then
            Market.requestedAt = NS.Now()
            NS.Log('requesting full listing')
            C_AuctionHouse.ReplicateItems()
        end
    end
end

-- Barrier for tests and diagnostics.
function Market.IsReading()
    return reader ~= nil
end
