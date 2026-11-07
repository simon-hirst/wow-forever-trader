-- Main: settings, events, the frame tick and slash commands.
local _, NS = ...

local DEFAULTS = {
    auto = true,        -- open the Sniper and start scanning when the auction house opens
    link = true,        -- draw the data strip for the desktop app
    walletStream = false, -- optional live balance updates while away from the AH
    autoConfirm = true, -- confirm the server's price by itself after a real press
    bets = true,        -- offer resale bets from the plan, not only below-vendor deals
    reserve = 500,      -- copper never spent
    stopAtSilver = 30,  -- keep the last 30s, stop Sniper when <=30s remains
    spec = 0.25,        -- share of free cash for one bet that is not below vendor price
    speed = 'normal',   -- 2.5s; live Fast scans were slower due to AH throttling
    pages = true,       -- experimental: examine high-value vendor deals between browse pages
    deadcache = true,   -- skip unchanged bargains after confirmed failed purchase attempts
}
-- The Windows Confirm button relays exactly one press of this key per click. Measured live
-- (2026-11-05): the game ignores F13-F24 entirely, but takes a posted numpad divide, which
-- Num Lock does not change and many keyboards do not have. Bound only while the AH is open.
local RELAY_KEY = 'NUMPADDIVIDE'

BINDING_HEADER_FOREVERTRADER = 'Forever Trader'
BINDING_NAME_FOREVERTRADER_CONFIRM = 'Confirm deal at cursor'

NS.AuctionHouseOpen = false

local settingsTable
function NS.Settings()
    if settingsTable then return settingsTable end
    local db = ForeverTraderBridgeDB
    if type(db) ~= 'table' then return DEFAULTS end
    db.settings = type(db.settings) == 'table' and db.settings or {}
    for key, value in pairs(DEFAULTS) do
        if db.settings[key] == nil then db.settings[key] = value end
    end
    settingsTable = db.settings
    return settingsTable
end

function NS.Now()
    return GetServerTime and GetServerTime() or time()
end

-- A hard bankroll floor is independent of the plan reserve and the desktop
-- phone-alert threshold. Never allow a plan sync to lower this protection.
-- /ft stopat off makes the hard floor zero, restoring existing reserve rules.
function NS.StopFloorCopper()
    local silver = tonumber(NS.Settings().stopAtSilver) or 0
    return math.max(0, math.floor(silver)) * 100
end

function NS.EffectiveReserve()
    local base = (NS.Plan and NS.Plan.reserve) or NS.Settings().reserve or 0
    return math.max(tonumber(base) or 0, NS.StopFloorCopper())
end

function NS.WalletFloorReached()
    local floor = NS.StopFloorCopper()
    return floor > 0 and GetMoney() <= floor
end

function NS.EnforceWalletFloor()
    if NS.WalletFloorReached() and NS.Aux and NS.Aux.StopForWalletFloor then
        NS.Aux.StopForWalletFloor()
    end
end

function NS.Log(text)
    text = tostring(text)
    NS.Link.Log(text)
    local db = ForeverTraderBridgeDB
    if type(db) == 'table' then
        db.log = db.log or {}
        db.log[#db.log + 1] = date('%H:%M:%S ') .. text
        while #db.log > 200 do table.remove(db.log, 1) end
    end
end

local function say(text)
    print('|cff66ccffForever Trader|r ' .. tostring(text))
end

-- Varints in a fixed order; trader/link.py reads them in the same order.
function NS.BuildState()
    local t, put = {}, NS.Link.PutVarint
    local S = NS.Snipe.state
    local aux = NS.Aux
    local flags = 0
    if NS.AuctionHouseOpen then flags = flags + 1 end
    if aux.IsReady() then flags = flags + 2 end
    if aux.IsRunning() then flags = flags + 4 end
    if NS.Settings().autoConfirm then flags = flags + 8 end
    if NS.Plan.rev > 0 then flags = flags + 16 end
    if S.deal and S.deal.commodity then flags = flags + 32 end
    if aux.IsTabShown() then flags = flags + 64 end
    put(t, NS.Now())
    put(t, flags)
    put(t, GetMoney())
    put(t, S.phase or 0)
    put(t, S.deal and S.deal.item_id or 0)
    put(t, S.n or 0)
    put(t, S.cost or 0)
    put(t, math.max(0, S.profit or 0))
    put(t, NS.Plan.rev)
    put(t, NS.Market.rounds)
    put(t, NS.Market.lastListingTs)
    put(t, NS.Link.QueueLength())
    local reason = S.deal and S.deal.deal_reason
    put(t, reason == 'vendor' and 1 or reason == 'usual' and 2 or reason == 'plan' and 3 or 0)
    put(t, S.deal and tonumber(S.deal.deal_percent) or 0)
    return NS.Link.BytesToString(t)
end

local relayBound = false
local function bindRelayKey()
    if relayBound or InCombatLockdown() then return end
    SetOverrideBindingClick(_G.ForeverTraderConfirmButton, true, RELAY_KEY, 'ForeverTraderConfirmButton', 'LeftButton')
    relayBound = true
end
local function unbindRelayKey()
    if not relayBound or InCombatLockdown() then return end
    ClearOverrideBindings(_G.ForeverTraderConfirmButton)
    relayBound = false
end

-- /ft relaytest: points candidate relay keys at a button that only logs, so the
-- Windows side can find a key the game accepts without anything being bought.
local RELAY_TEST_KEYS = {RELAY_KEY, 'F12', 'NUMPAD5'}
local relayTester, relayTestUntil
local relayTestRun = 0
local function relayTest(seconds)
    say('relay test: starting')
    if InCombatLockdown() then say('relay test: not in combat') return end
    local ok, err = pcall(function()
        if not relayTester then
            relayTester = CreateFrame('Button', 'ForeverTraderRelayTest', UIParent)
            relayTester:SetScript('OnClick', function(_, key) NS.Log('relay test: ' .. tostring(key) .. ' arrived') say('relay test: ' .. tostring(key) .. ' arrived') end)
        end
        unbindRelayKey()
    end)
    if not ok then say('relay test failed to set up: ' .. tostring(err)) NS.Log('relay test setup error: ' .. tostring(err)) return end
    local names = {}
    for _, key in ipairs(RELAY_TEST_KEYS) do
        local bound, berr = pcall(SetOverrideBindingClick, relayTester, true, key, 'ForeverTraderRelayTest', key)
        -- The second argument makes it report override bindings, which these are.
        local action = bound and GetBindingByKey and GetBindingByKey(key, true)
        names[#names + 1] = key .. '=' .. (bound and tostring(action) or ('error ' .. tostring(berr)))
    end
    NS.Log('relay test armed for ' .. seconds .. 's: ' .. table.concat(names, ' '))
    say('relay test armed for ' .. seconds .. ' seconds: ' .. table.concat(names, ' '))
    relayTestUntil = GetTime() + seconds
    relayTestRun = relayTestRun + 1
    local run = relayTestRun
    C_Timer.After(seconds, function()
        -- A newer test owns the bindings now; leave them to its own timer.
        if run ~= relayTestRun or InCombatLockdown() then return end
        pcall(ClearOverrideBindings, relayTester)
        relayTestUntil = nil
        if NS.AuctionHouseOpen then bindRelayKey() end
        NS.Log('relay test over')
        say('relay test over')
    end)
end

local function onAuctionHouseShow()
    NS.AuctionHouseOpen = true
    NS.Market.SessionStart()
    NS.Link.Open()
    if not relayTestUntil then bindRelayKey() end
    -- aux shows its Search tab on this same event, so go after it.
    C_Timer.After(0.3, function()
        if NS.AuctionHouseOpen then NS.Aux.StartSession() end
    end)
end

local function onAuctionHouseClosed()
    NS.AuctionHouseOpen = false
    NS.Market.SessionEnd()
    if not NS.Settings().walletStream then NS.Link.Close() end
    unbindRelayKey()
end

local events = CreateFrame('Frame')
for _, event in ipairs({
    'ADDON_LOADED', 'PLAYER_LOGIN', 'PLAYER_MONEY', 'AUCTION_HOUSE_SHOW', 'AUCTION_HOUSE_CLOSED', 'REPLICATE_ITEM_LIST_UPDATE',
    'COMMODITY_PRICE_UPDATED', 'COMMODITY_PURCHASE_SUCCEEDED', 'COMMODITY_PURCHASE_FAILED', 'AUCTION_HOUSE_SHOW_ERROR',
    'ADDON_ACTION_BLOCKED', 'ADDON_ACTION_FORBIDDEN',
}) do
    NS.Listen(events, event)
end
events:SetScript('OnEvent', function(_, event, ...)
    if event == 'ADDON_LOADED' then
        if ... == 'ForeverTraderBridge' then
            NS.Settings()
            NS.Snipe.Create()
        end
    elseif event == 'PLAYER_LOGIN' then
        NS.Plan.LoadNext()
        if NS.Settings().walletStream then NS.Link.Open() end
    elseif event == 'PLAYER_MONEY' then
        -- Wallet change only: update the existing coloured strip. No AH request.
        NS.Link.MarkState()
        NS.EnforceWalletFloor()
    elseif event == 'AUCTION_HOUSE_SHOW' then
        onAuctionHouseShow()
    elseif event == 'AUCTION_HOUSE_CLOSED' then
        onAuctionHouseClosed()
    elseif event == 'REPLICATE_ITEM_LIST_UPDATE' then
        NS.Market.OnReplicateUpdate()
    else
        NS.Snipe.OnEvent(event, ...)
    end
end)

local ticker = CreateFrame('Frame')
ticker:SetScript('OnUpdate', function(_, elapsed)
    NS.Market.Tick()
    NS.Plan.Tick()
    NS.EnforceWalletFloor()
    NS.Snipe.Tick()
    NS.Link.Tick(elapsed)
end)

local function diagnostics()
    local itemName = NS.GetItemInfo and select(1, NS.GetItemInfo(2589))
    local vendor = NS.GetItemInfo and select(11, NS.GetItemInfo(2589))
    local attached = NS.Aux.Attach()
    local lines = {
        'item info: ' .. tostring(itemName) .. ', vendor ' .. tostring(vendor),
        'aux Sniper: ' .. (attached and ('attached, tab ' .. tostring(NS.Aux.tabIndex)) or 'NOT FOUND') ..
            ', running ' .. tostring(NS.Aux.IsRunning()),
        'auction house open: ' .. tostring(NS.AuctionHouseOpen) .. ', scan rounds ' .. NS.Market.rounds,
        'last full listing: ' .. NS.Market.lastListingRows .. ' auctions this session, next request in ' ..
            math.ceil(NS.Market.NextListingIn() / 60) .. ' min',
        'link: ' .. (NS.Link.IsActive() and 'drawing' or 'off') .. ', frames sent ' .. NS.Link.sent ..
            ', queued ' .. NS.Link.QueueLength() .. ', critical queued ' ..
            NS.Link.UrgentQueueLength() .. ', critical sent ' .. NS.Link.urgentSent ..
            ', critical dropped ' .. NS.Link.droppedUrgent,
        'plan: rev ' .. NS.Plan.rev .. ', limits ' .. tostring(NS.Plan.count or 0) .. ', slot ' .. NS.Plan.slot,
        'autoConfirm: ' .. tostring(NS.Settings().autoConfirm) .. ', bets ' .. tostring(NS.Settings().bets) .. ', reserve ' .. NS.Snipe.Coin(NS.EffectiveReserve()),
        'sniper wallet floor: ' .. NS.Snipe.Coin(NS.StopFloorCopper()) ..
            ', reached ' .. tostring(NS.WalletFloorReached()) .. ', stopped ' .. tostring(NS.Aux.walletFloorStopped),
        'confirm phase: ' .. tostring(NS.Snipe.state.phase),
        'sniper scan: ' .. NS.Aux.StatsLine(),
        'auction-house reliability: ' .. NS.Aux.FaultStats(),
        -- GetBindingByKey does not report override click bindings in this client, so show our own flag.
        'relay key ' .. RELAY_KEY .. ': ' .. (relayBound and 'bound to Confirm' or 'not bound'),
        'unknown events:' .. (NS.unknownEvents or ' none'),
    }
    for _, line in ipairs(lines) do
        say(line)
        NS.Log('diag ' .. line)
    end
end

SLASH_FOREVERTRADER1 = '/ft'
SlashCmdList.FOREVERTRADER = function(input)
    local command, rest = string.match(input or '', '^%s*(%S*)%s*(.-)%s*$')
    command = string.lower(command or '')
    local settings = NS.Settings()
    if command == 'diag' then
        diagnostics()
    elseif command == 'scanstats' then
        say(NS.Aux.StatsLine())
    elseif command == 'pagestats' then
        say(NS.Aux.PreviewStats())
    elseif command == 'faultstats' then
        say(NS.Aux.FaultStats())
    elseif command == 'pages' then
        if rest == 'on' or rest == 'off' then
            NS.Aux.SetPages(rest == 'on')
            say('incremental pages: ' .. (NS.Aux.PagesOn() and 'on' or 'off') ..
                ' (server throttle unchanged; /ft pagestats shows early checks)')
        else
            say('usage: /ft pages on|off')
        end
    elseif command == 'deadcache' then
        if rest == 'on' or rest == 'off' then
            NS.Aux.SetDeadCache(rest == 'on')
            say('unchanged dead-auction cooldown: ' ..
                (NS.Aux.DeadCacheOn() and 'on' or 'off') ..
                ' (/ft pagestats for suppressed candidate counters)')
        else
            say('usage: /ft deadcache on|off')
        end
    elseif command == 'stopat' then
        if rest == '' then
            say('sniping wallet floor: ' ..
                (NS.StopFloorCopper() > 0 and NS.Snipe.Coin(NS.StopFloorCopper()) or 'off') ..
                '; reserve ' .. NS.Snipe.Coin(NS.EffectiveReserve()) ..
                ', sniper running ' .. tostring(NS.Aux.IsRunning()))
        elseif rest == 'off' then
            settings.stopAtSilver = 0
            say('sniping wallet floor off; configured reserve still applies')
        else
            local silver = tonumber(rest)
            if silver and silver == math.floor(silver) and silver >= 1 and silver <= 1000000 then
                settings.stopAtSilver = silver
                say('sniping stops at or below ' .. NS.Snipe.Coin(NS.StopFloorCopper()) ..
                    '; purchases cannot reduce wallet below this amount')
                NS.EnforceWalletFloor()
            else
                say('usage: /ft stopat <silver> (e.g. /ft stopat 30) | off')
            end
        end
    elseif command == 'walletstream' then
        if rest == 'on' or rest == 'off' then
            settings.walletStream = rest == 'on'
            if settings.walletStream or NS.AuctionHouseOpen then
                NS.Link.Open()
            else
                NS.Link.Close()
            end
            say('live wallet stream outside AH: ' .. (settings.walletStream and 'on' or 'off'))
        else
            say('usage: /ft walletstream on|off')
        end
    elseif command == 'speed' then
        if NS.Aux.SetSpeed(rest) then
            say('scan cooldown: ' .. NS.Aux.Speed() ..
                ' (still throttled by the server; /ft scanstats shows where time goes)')
        else
            say('usage: /ft speed fast|normal|safe')
        end
    elseif command == 'auto' or command == 'link' or command == 'autoconfirm' or command == 'bets' then
        local key = command == 'autoconfirm' and 'autoConfirm' or command
        settings[key] = rest == 'on' or (rest ~= 'off' and not settings[key])
        say(key .. ': ' .. (settings[key] and 'on' or 'off'))
        if key == 'link' then
            if settings.link and (NS.AuctionHouseOpen or settings.walletStream) then NS.Link.Open() elseif not settings.link then NS.Link.Stop() end
        elseif key == 'auto' and settings.auto and NS.Aux then
            -- Explicit manual /ft auto on is the only automatic-start latch
            -- reset after the wallet floor was reached. Cash increases do not
            -- silently restart Sniper scanning.
            NS.Aux.walletFloorStopped = false
            if NS.AuctionHouseOpen then NS.Aux.StartSession() end
        end
    elseif command == 'reserve' then
        local copper = tonumber(rest)
        if copper and copper >= 0 then
            settings.reserve = math.floor(copper)
            say('reserve: ' .. NS.Snipe.Coin(settings.reserve))
        else
            say('usage: /ft reserve <copper>')
        end
    elseif command == 'relaytest' then
        relayTest(tonumber(rest) or 60)
    elseif command == 'linktest' then
        local count = tonumber(rest) or 150
        settings.link = true
        NS.Link.Open()
        NS.Link.TestBurst(count)
        if not NS.AuctionHouseOpen then NS.Link.Close() end
        say('sending ' .. count .. ' test frames to the app')
    elseif command == 'testplan' then
        local id, limit, units = string.match(rest, '^(%d+)%s+(%d+)%s+(%d+)$')
        if id then
            NS.Plan.SetTest(tonumber(id), tonumber(limit), tonumber(units))
            say(string.format('test limit: item %s at or below %sc, at most %s units. The Confirm button appears when the next round finds it.', id, limit, units))
        else
            say('usage: /ft testplan <itemID> <limit copper> <units>')
        end
    elseif command == 'log' then
        for _, line in ipairs((ForeverTraderBridgeDB and ForeverTraderBridgeDB.log) or {}) do print(line) end
    else
        say('/ft diag | scanstats | pagestats | faultstats | walletstream on|off | pages on|off | deadcache on|off | speed fast|normal|safe | auto [on|off] | link [on|off] | autoconfirm [on|off] | bets [on|off] | reserve <copper> | stopat <silver>|off | linktest | relaytest | testplan <id> <limit> <units> | log')
    end
end
