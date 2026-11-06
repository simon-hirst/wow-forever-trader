-- A small stand-in for the WoW client, for running addon code outside the game.
-- Widgets accept any CamelCase method as a no-op; only behaviour the tests depend on
-- is real. The auction house below acts like the server, including the rule that a
-- purchase cannot start without a hardware event.

__now = 1000.0
__server0 = 1793870000
__money = 100000
__hardware = false
__violations = {}
__prints = {}
__frames = {}
__timers = {}
__blocked = {}

function GetTime() return __now end
function GetServerTime() return __server0 + math.floor(__now) end
function time() return __server0 + math.floor(__now) end
date = os.date
function debugprofilestop() return __now * 1000 end
function GetMoney() return __money end
function GetRealmName() return 'SimRealm' end
function UnitName() return 'Tester' end
function UnitFactionGroup() return 'Horde' end
function GetCursorPosition() return 500, 400 end
function GetPhysicalScreenSize() return 1920, 1080 end
function InCombatLockdown() return false end
function GetLocale() return 'enUS' end
function GetBuildInfo() return '1.60.1', '70790', 'Nov 1 2026', 16001 end
function FlashClientIcon() end
function PlaySound() end
function hooksecurefunc(a, b, c)
    local t, name, f = a, b, c
    if type(a) == 'string' then t, name, f = _G, a, b end
    local old = t[name]
    t[name] = function(...)
        local r = {old(...)}
        f(...)
        return unpack(r)
    end
end
-- Override bindings belong to an owner, as in the game; a key the game knows maps to 'CLICK name:button'.
__bindings = {}
function SetOverrideBindingClick(owner, priority, key, name, mouseButton)
    __bindings[key] = {owner = owner, action = 'CLICK ' .. name .. ':' .. (mouseButton or 'LeftButton')}
end
function ClearOverrideBindings(owner)
    for key, b in pairs(__bindings) do
        if b.owner == owner then __bindings[key] = nil end
    end
end
function GetBindingByKey(key, checkOverride)
    -- As in the game: override bindings are only reported when asked for.
    return checkOverride and __bindings[key] and __bindings[key].action or nil
end
-- A relayed key press: what the game does when a bound key arrives.
function __relay_key(key)
    local b = __bindings[key]
    if not b then return false end
    local name, mouseButton = string.match(b.action, '^CLICK ([^:]+):(.+)$')
    _G[name]:Click(mouseButton)
    return true
end
function print(...)
    local parts = {}
    for i = 1, select('#', ...) do parts[i] = tostring(select(i, ...)) end
    __prints[#__prints + 1] = table.concat(parts, ' ')
end
wipe = function(t) for k in pairs(t) do t[k] = nil end return t end
tinsert, tremove, sort, getn = table.insert, table.remove, table.sort, table.getn
floor, ceil, min, max, abs, mod = math.floor, math.ceil, math.min, math.max, math.abs, math.fmod
format, strlen, strfind, strmatch, strsub, strlower, strupper, gsub, strrep, strbyte, strchar, gmatch =
    string.format, string.len, string.find, string.match, string.sub, string.lower, string.upper, string.gsub,
    string.rep, string.byte, string.char, string.gmatch
function strtrim(s) return (string.gsub(s, '^%s*(.-)%s*$', '%1')) end
function strsplit(sep, s)
    local out, pos = {}, 1
    while true do
        local a, b = string.find(s, sep, pos, true)
        if not a then out[#out + 1] = string.sub(s, pos); break end
        out[#out + 1] = string.sub(s, pos, a - 1)
        pos = b + 1
    end
    return unpack(out)
end
function tContains(t, v) for _, x in pairs(t) do if x == v then return true end end return false end
function CopyTable(t) local o = {}; for k, v in pairs(t) do o[k] = type(v) == 'table' and CopyTable(v) or v end return o end
function CreateColor(r, g, b, a)
    return {r = r, g = g, b = b, a = a, GetRGB = function() return r, g, b end, GetRGBA = function() return r, g, b, a end}
end
C_Timer = {
    After = function(seconds, f) __timers[#__timers + 1] = {at = __now + seconds, f = f} end,
    NewTicker = function() return {Cancel = function() end} end,
    NewTimer = function() return {Cancel = function() end} end,
}
function __runTimers()
    for i = #__timers, 1, -1 do
        local t = __timers[i]
        if t.at <= __now then
            table.remove(__timers, i)
            t.f()
        end
    end
end

-- Widgets ---------------------------------------------------------------------
noop = function() end
local Widget = {}
local WidgetMT = {}
WidgetMT.__index = function(self, key)
    local v = Widget[key]
    if v ~= nil then return v end
    if type(key) == 'string' and string.match(key, '^%u') then return noop end
    return nil
end

function CreateFrame(kind, name, parent, template)
    local f = setmetatable({__kind = kind, __scripts = {}, __events = {}, __shown = true, __children = {}, __text = ''}, WidgetMT)
    f.__parent = parent
    if parent and parent.__children then table.insert(parent.__children, f) end
    if name then _G[name] = f end
    f.__name = name
    if kind == 'Button' then
        f.__fontstring = nil
        f.__enabled = true
    end
    table.insert(__frames, f)
    return f
end
UIParent = CreateFrame('Frame', 'UIParent')
WorldFrame = CreateFrame('Frame', 'WorldFrame')
GameTooltip = CreateFrame('GameTooltip', 'GameTooltip')
UIErrorsFrame = CreateFrame('Frame', 'UIErrorsFrame')
DEFAULT_CHAT_FRAME = CreateFrame('Frame', 'DEFAULT_CHAT_FRAME')
DEFAULT_CHAT_FRAME.AddMessage = function(_, text) __prints[#__prints + 1] = tostring(text) end

function Widget:SetScript(name, f) self.__scripts[name] = f end
function Widget:GetScript(name) return self.__scripts[name] end
function Widget:HookScript(name, f)
    local old = self.__scripts[name]
    self.__scripts[name] = old and function(...) old(...); f(...) end or f
end
function Widget:RegisterEvent(e) self.__events[e] = true end
function Widget:UnregisterEvent(e) self.__events[e] = nil end
function Widget:RegisterAllEvents() end
function Widget:Show()
    local was = self.__shown
    self.__shown = true
    if not was and self.__scripts.OnShow then self.__scripts.OnShow(self) end
end
function Widget:Hide()
    local was = self.__shown
    self.__shown = false
    if was and self.__scripts.OnHide then self.__scripts.OnHide(self) end
end
function Widget:IsShown() return self.__shown end
function Widget:IsVisible()
    local f = self
    while f do
        if not f.__shown then return false end
        f = f.__parent
    end
    return true
end
function Widget:SetParent(p) self.__parent = p end
function Widget:GetParent() return self.__parent end
function Widget:GetChildren() return unpack(self.__children) end
function Widget:GetEffectiveScale() return 1 end
function Widget:GetScale() return 1 end
function Widget:GetWidth() return self.__width or 100 end
function Widget:GetHeight() return self.__height or 20 end
function Widget:SetWidth(v) self.__width = v end
function Widget:SetHeight(v) self.__height = v end
function Widget:SetSize(w, h) self.__width, self.__height = w, h end
function Widget:GetLeft() return 0 end
function Widget:GetRight() return 100 end
function Widget:GetTop() return 100 end
function Widget:GetBottom() return 0 end
function Widget:GetCenter() return 50, 50 end
function Widget:GetNumPoints() return 0 end
function Widget:GetPoint() return nil end
function Widget:IsMouseOver() return false end
function Widget:IsEnabled() return self.__enabled ~= false end
function Widget:Enable() self.__enabled = true end
function Widget:Disable() self.__enabled = false end
function Widget:GetName() return self.__name end
function Widget:GetObjectType() return self.__kind end
function Widget:GetStringWidth() return 40 end
function Widget:GetStringHeight() return 12 end
function Widget:GetFont() return 'font', 12 end
function Widget:GetFrameLevel() return 1 end
function Widget:GetTextColor() return 1, 1, 1, 1 end
function Widget:GetBackdropColor() return 0, 0, 0, 1 end
function Widget:GetBackdropBorderColor() return 0, 0, 0, 1 end
function Widget:SetText(text)
    self.__text = text == nil and '' or tostring(text)
    if self.__scripts.OnTextChanged then self.__scripts.OnTextChanged(self, false) end
end
function Widget:GetText() return self.__text end
function Widget:GetNumber() return tonumber(self.__text) or 0 end
function Widget:GetFontString()
    if not self.__fontstring then self.__fontstring = self:CreateFontString() end
    return self.__fontstring
end
-- Texture slots on buttons and check buttons hand back a real object.
for _, slot in ipairs({'Normal', 'Pushed', 'Highlight', 'Disabled', 'Checked', 'DisabledChecked', 'Thumb'}) do
    Widget['Get' .. slot .. 'Texture'] = function(self)
        local key = '__tex' .. slot
        if not self[key] then self[key] = self:CreateTexture() end
        return self[key]
    end
end
function Widget:CreateTexture()
    local t = setmetatable({__kind = 'Texture', __scripts = {}, __events = {}, __shown = true, __children = {}, __parent = self, __text = ''}, WidgetMT)
    t.SetColorTexture = function(tex, r, g, b, a) tex.__color = {r, g, b, a} end
    return t
end
function Widget:CreateFontString()
    return setmetatable({__kind = 'FontString', __scripts = {}, __events = {}, __shown = true, __children = {}, __parent = self, __text = ''}, WidgetMT)
end
function Widget:Click(button)
    if self.__enabled == false then return end
    if self.__scripts.OnClick then self.__scripts.OnClick(self, button or 'LeftButton', false) end
end

function __fire(event, ...)
    for i = 1, #__frames do
        local f = __frames[i]
        if f.__events[event] and f.__scripts.OnEvent then f.__scripts.OnEvent(f, event, ...) end
    end
end

-- One rendered frame. As in the game, a hidden frame's OnUpdate does not run.
function __frame(elapsed)
    __now = __now + elapsed
    __runTimers()
    __simTick()
    for i = 1, #__frames do
        local f = __frames[i]
        local script = f.__scripts.OnUpdate
        if script and f:IsVisible() then script(f, elapsed) end
    end
end

-- A real press: only here does the server accept a purchase starting.
function __press(f)
    __hardware = true
    local ok, err = pcall(f)
    __hardware = false
    if not ok then error(err) end
end

-- Item data -------------------------------------------------------------------
__items = {}
local function item_tuple(i)
    return i.name, 'item:' .. i.id, i.quality or 1, i.level or 10, i.req or 1, i.class or 'Trade Goods', i.sub or 'Cloth',
        i.stack or 20, i.slot or '', 134400, i.vendor or 0, 7, 0, 1, 0, 0, 0, false
end
local function lookup(arg)
    if type(arg) == 'number' then return __items[arg] end
    if type(arg) == 'string' then
        local id = tonumber(string.match(arg, 'item:(%d+)'))
        return id and __items[id]
    end
end
C_Item = {
    GetItemInfo = function(arg)
        local i = lookup(arg)
        if i then return item_tuple(i) end
    end,
    GetItemInfoInstant = function(arg)
        local i = lookup(arg)
        if i then return i.id, i.class or 'Trade Goods', i.sub or 'Cloth', i.slot or '', 134400, 7, 0 end
    end,
    IsItemDataCachedByID = function(id) return __items[id] ~= nil end,
    RequestLoadItemDataByID = noop,
    GetItemQualityColor = function() return 1, 1, 1, 'ffffffff' end,
    GetItemNameByID = function(id) return __items[id] and __items[id].name end,
    GetDetailedItemLevelInfo = function(arg) local i = lookup(arg); return i and (i.level or 10) end,
    GetItemSpell = noop,
    GetItemClassInfo = noop,
    GetItemSubClassInfo = noop,
}
ITEM_QUALITY_COLORS = setmetatable({}, {__index = function() return {r = 1, g = 1, b = 1, hex = '|cffffffff'} end})

-- The auction house server ------------------------------------------------------
__ah = {commodity = {}, gear = {}, quote = nil, latency = 0.2, pending = {}, listing = {}}
__ah_log = {}

local function later(seconds, f) table.insert(__ah.pending, {at = __now + seconds, f = f}) end

function __simTick()
    for i = #__ah.pending, 1, -1 do
        local p = __ah.pending[i]
        if p.at <= __now then
            table.remove(__ah.pending, i)
            p.f()
        end
    end
end

local function commodityQuantity(id)
    local total = 0
    for _, t in ipairs(__ah.commodity[id] or {}) do total = total + t.q end
    return total
end

local function quoteFor(id, n)
    local left, total = n, 0
    local tiers = __ah.commodity[id] or {}
    table.sort(tiers, function(a, b) return a.p < b.p end)
    for _, t in ipairs(tiers) do
        local take = math.min(left, t.q)
        total = total + take * t.p
        left = left - take
    end
    if left > 0 then return nil end
    return total
end

local function needHardware(name)
    if not __hardware then
        table.insert(__violations, name)
        __fire('ADDON_ACTION_FORBIDDEN', 'ForeverTraderBridge', name)
        return false
    end
    return true
end

local function browseRows()
    local rows = {}
    for id, tiers in pairs(__ah.commodity) do
        local low, total
        for _, t in ipairs(tiers) do
            if t.q > 0 then
                total = (total or 0) + t.q
                if not low or t.p < low then low = t.p end
            end
        end
        if total then rows[#rows + 1] = {itemKey = {itemID = id, itemLevel = 0, itemSuffix = 0, battlePetSpeciesID = 0}, totalQuantity = total, minPrice = low, containsOwnerItem = false} end
    end
    for id, auctions in pairs(__ah.gear) do
        local low
        for _, a in ipairs(auctions) do
            if not low or a.buyout < low then low = a.buyout end
        end
        if low then rows[#rows + 1] = {itemKey = {itemID = id, itemLevel = 0, itemSuffix = 0, battlePetSpeciesID = 0}, totalQuantity = #auctions, minPrice = low, containsOwnerItem = false} end
    end
    return rows
end

-- A paginated browse simulator. The real realm sends one result page at a
-- time; item searches can be interleaved only after a page response.
-- Default pageSize=0 keeps the pre-existing one-response tests unchanged.
local function filteredBrowseRows()
    local rows, q = browseRows(), __ah.browseQuery
    if q and q.searchString and q.searchString ~= '' then
        local filtered = {}
        for _, r in ipairs(rows) do
            local item = __items[r.itemKey.itemID]
            if item and string.lower(item.name) == string.lower(q.searchString) then
                filtered[#filtered + 1] = r
            end
        end
        rows = filtered
    end
    table.sort(rows, function(a, b)
        if a.minPrice == b.minPrice then return a.itemKey.itemID < b.itemKey.itemID end
        return a.minPrice < b.minPrice
    end)
    return rows
end

local function browseVisibleRows()
    local rows = filteredBrowseRows()
    if (__ah.browsePageSize or 0) <= 0 then return rows end
    local visible = (__ah.browsePageIndex or 0) * __ah.browsePageSize
    local out = {}
    for i = 1, math.min(visible, #rows) do out[#out + 1] = rows[i] end
    return out
end

C_AuctionHouse = {
    IsThrottledMessageSystemReady = function() return true end,
    SupportsCopperValues = function() return true end,
    QueryOwnedAuctions = noop, QueryBids = noop, CloseAuctionHouse = noop,
    GetNumOwnedAuctions = function() return 0 end, GetNumBids = function() return 0 end,
    SendBrowseQuery = function(query)
        __ah.browseQuery = query
        __ah.browsePageIndex = 0
        __ah_log[#__ah_log + 1] = 'browse'
        if __ah.dropBrowseAnswers then return end
        later(__ah.browseLatency or __ah.latency, function()
            __ah.browsePageIndex = 1
            __fire('AUCTION_HOUSE_BROWSE_RESULTS_UPDATED')
        end)
    end,
    HasFullBrowseResults = function()
        if (__ah.browsePageSize or 0) <= 0 then return true end
        return (__ah.browsePageIndex or 0) * __ah.browsePageSize >= #filteredBrowseRows()
    end,
    RequestMoreBrowseResults = function()
        __ah_log[#__ah_log + 1] = 'browse_more'
        if __ah.dropBrowseAnswers then return end
        later(__ah.browseLatency or __ah.latency, function()
            __ah.browsePageIndex = (__ah.browsePageIndex or 0) + 1
            __fire('AUCTION_HOUSE_BROWSE_RESULTS_ADDED')
        end)
    end,
    GetBrowseResults = browseVisibleRows,
    GetItemKeyInfo = function(key)
        local i = __items[key.itemID]
        if not i then return nil end
        return {itemID = key.itemID, itemName = i.name, quality = i.quality or 1, isCommodity = (i.stack or 1) > 1, isPet = false, iconFileID = 134400}
    end,
    GetItemKeyFromItem = noop,
    MakeItemKey = function(id) return {itemID = id, itemLevel = 0, itemSuffix = 0, battlePetSpeciesID = 0} end,
    GetItemKeyRequiredLevel = function() return 1 end,
    SendSearchQuery = function(key)
        __ah_log[#__ah_log + 1] = 'search:' .. key.itemID
        if __ah.invalidateBrowseOnSearch then __ah.browsePageIndex = 0 end
        later(__ah.latency, function()
            if (__items[key.itemID].stack or 1) > 1 then
                __fire('COMMODITY_SEARCH_RESULTS_UPDATED', key.itemID)
            else
                __fire('ITEM_SEARCH_RESULTS_UPDATED', key)
            end
        end)
    end,
    HasFullCommoditySearchResults = function() return true end,
    GetCommoditySearchResultsQuantity = function(id) return commodityQuantity(id) end,
    RequestMoreCommoditySearchResults = noop,
    GetNumCommoditySearchResults = function(id)
        local tiers = {}
        for _, t in ipairs(__ah.commodity[id] or {}) do if t.q > 0 then tiers[#tiers + 1] = t end end
        __ah.commodity[id] = tiers
        return #tiers
    end,
    GetCommoditySearchResultInfo = function(id, i)
        local tiers = __ah.commodity[id]
        table.sort(tiers, function(a, b) return a.p < b.p end)
        local t = tiers[i]
        if t then
            return {itemID = id, quantity = t.q, unitPrice = t.p, auctionID = 1000 + i, owners = {'someone'}, totalNumberOfOwners = 1,
                    timeLeftSeconds = 7200, numOwnerItems = 0, containsOwnerItem = false, containsAccountItem = false}
        end
    end,
    HasSearchResults = function() return true end,
    HasFullItemSearchResults = function() return true end,
    GetItemSearchResultsQuantity = function(key) return #(__ah.gear[key.itemID] or {}) end,
    RequestMoreItemSearchResults = noop,
    GetNumItemSearchResults = function(key) return #(__ah.gear[key.itemID] or {}) end,
    GetItemSearchResultInfo = function(key, i)
        local a = (__ah.gear[key.itemID] or {})[i]
        if a then
            return {itemKey = key, itemLink = 'item:' .. key.itemID, quantity = 1, auctionID = a.id, buyoutAmount = a.buyout, bidAmount = nil, minBid = nil,
                    owners = {'someone'}, totalNumberOfOwners = 1, timeLeft = 2, containsOwnerItem = false, containsAccountItem = false}
        end
    end,
    StartCommoditiesPurchase = function(id, n)
        __ah_log[#__ah_log + 1] = 'start:' .. id .. ':' .. n
        if not needHardware('StartCommoditiesPurchase') then return end
        __ah.quote = {id = id, n = n}
        later(__ah.latency, function()
            local total = quoteFor(id, n)
            if total then
                __ah.quote.total = total
                __fire('COMMODITY_PRICE_UPDATED', math.ceil(total / n), total)
            else
                __ah.quote = nil
                __fire('COMMODITY_PRICE_UNAVAILABLE')
            end
        end)
    end,
    ConfirmCommoditiesPurchase = function(id, n)
        __ah_log[#__ah_log + 1] = 'confirm:' .. id .. ':' .. n
        if __ah.confirmNeedsHardware and not needHardware('ConfirmCommoditiesPurchase') then return end
        local q = __ah.quote
        later(__ah.latency, function()
            if __ah.dropAnswers then return end
            if not q or q.id ~= id or q.n ~= n then
                __fire('COMMODITY_PURCHASE_FAILED')
                return
            end
            local total = quoteFor(id, n)
            if not total or total > q.total then
                __fire('COMMODITY_PURCHASE_FAILED')
                return
            end
            local left = n
            for _, t in ipairs(__ah.commodity[id]) do
                local take = math.min(left, t.q)
                t.q = t.q - take
                left = left - take
            end
            __money = __money - total
            __ah.bought = __ah.bought or {}
            table.insert(__ah.bought, {id = id, n = n, total = total})
            __fire('PLAYER_MONEY')
            __fire('COMMODITY_PURCHASE_SUCCEEDED')
            -- The client also reports the purchase in two more ways; receipts must count it once.
            if __ah.noisyPurchaseEvents then
                __fire('AUCTION_HOUSE_PURCHASE_COMPLETED', 777000 + #__ah.bought)
                __fire('COMMODITY_PURCHASED', id, n)
            end
        end)
    end,
    CancelCommoditiesPurchase = function()
        __ah_log[#__ah_log + 1] = 'cancel'
        __ah.quote = nil
    end,
    GetQuoteDurationRemaining = function() return __ah.quote and 30 or 0 end,
    PlaceBid = function(auctionID, amount)
        __ah_log[#__ah_log + 1] = 'bid:' .. auctionID .. ':' .. tostring(amount)
        if not needHardware('PlaceBid') then return end
        later(__ah.latency, function()
            for id, auctions in pairs(__ah.gear) do
                for i, a in ipairs(auctions) do
                    if a.id == auctionID then
                        table.remove(auctions, i)
                        __money = __money - amount
                        __ah.bought = __ah.bought or {}
                        table.insert(__ah.bought, {id = id, n = 1, total = amount, auction = auctionID})
                        __fire('PLAYER_MONEY')
                        __fire('AUCTION_HOUSE_PURCHASE_COMPLETED', auctionID)
                        return
                    end
                end
            end
            __fire('AUCTION_HOUSE_SHOW_ERROR', 1)
        end)
    end,
    ReplicateItems = function()
        -- The server answers one full listing per 15 minutes and silently ignores the rest.
        if __ah.replicatedAt and __now - __ah.replicatedAt < 900 then
            __ah_log[#__ah_log + 1] = 'replicate ignored'
            return
        end
        __ah.replicatedAt = __now
        __ah_log[#__ah_log + 1] = 'replicate'
        later(__ah.latency, function() __fire('REPLICATE_ITEM_LIST_UPDATE') end)
    end,
    GetNumReplicateItems = function() return #__ah.listing end,
    GetReplicateItemInfo = function(index)
        local r = __ah.listing[index + 1]
        if not r then return end
        return 'name', 134400, r.count, 1, true, 1, '', 0, 0, r.buyout, 0, false, nil, nil, nil, 0, r.id, true
    end,
    GetReplicateItemTimeLeft = function(index) local r = __ah.listing[index + 1]; return r and r.band or 0 end,
    GetReplicateItemLink = function(index) local r = __ah.listing[index + 1]; return r and ('item:' .. r.id) end,
    GetTimeLeftBandInfo = function() return 0, 1800 end,
    GetCancelCost = function() return 0 end,
    CanCancelAuction = function() return false end,
    IsSellItemValid = function() return false end,
}
C_Container = {GetContainerNumSlots = function() return 0 end}
C_AddOns = {LoadAddOn = function(name)
    __loaded = __loaded or {}
    __loaded[#__loaded + 1] = name
    if __planFile then
        local f = assert(loadstring(__planFile))
        f()
        return true
    end
    return false, 'MISSING'
end}
C_CurrencyInfo = {GetCoinTextureString = function(c) return tostring(c) .. 'c' end}
C_Sound = {PlaySound = noop}
SOUNDKIT = setmetatable({}, {__index = function() return 1 end})
AUCTION_CATEGORIES = {}
AuctionCategories = {}
Enum = setmetatable({}, {__index = function(t, k)
    local e = setmetatable({}, {__index = function(_, name) return name end})
    t[k] = e
    return e
end})
LIGHTYELLOW_FONT_COLOR_CODE = ''
ERR_AUCTION_BID_PLACED = 'Bid accepted.'
ERR_NOT_ENOUGH_MONEY = 'Not enough money'
SlashCmdList = {}

UISpecialFrames = {}
StaticPopupDialogs = {}
StaticPopup_Show = noop
STANDARD_TEXT_FONT = 'font'
SlashCmdList = SlashCmdList or {}
hash_SlashCmdList = {}
FONT_COLOR_CODE_CLOSE = '|r'
NORMAL_FONT_COLOR = CreateColor(1, 1, 1, 1)

-- Blizzard's localised text constants: any ALL_CAPS global reads as its own name.
-- Blizzard UI helpers (FauxScrollFrame_Update and the like) do nothing.
function FauxScrollFrame_GetOffset() return 0 end
setmetatable(_G, {__index = function(_, key)
    if type(key) ~= 'string' then return nil end
    if string.match(key, '^[A-Z][A-Z0-9_]+$') then return key end
    if string.match(key, '^%u%w+_%u%w*$') then return noop end
    -- Template frames get children named after them, like <name>ScrollBar.
    if string.match(key, 'ScrollBar$') or string.match(key, 'ScrollUpButton$') or string.match(key, 'ScrollDownButton$')
        or string.match(key, '%dIcon$') or string.match(key, '%dCount$') then
        local child = CreateFrame('Frame', key)
        rawset(_G, key, child)
        return child
    end
end})

TooltipDataProcessor = {AddTooltipPostCall = noop}
C_PlayerInfo = {CanUseItem = function() return true end}
