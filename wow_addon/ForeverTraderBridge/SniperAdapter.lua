-- Aux: connects to auxForever's Sniper without editing its files, so an aux update
-- from CurseForge cannot wipe this out.
--
-- aux keeps each module's state in a private environment. The Sniper frame's
-- OnUpdate script runs in that environment, and getfenv hands it back.
local _, NS = ...
local Aux = {}
NS.Aux = Aux

local SNIPER_TAB_NAME = 'Sniper'
local SNIPER_TAB_FALLBACK = 2

-- The full 4,000+ item browse is server paginated and can dominate a Sniper
-- round. Shorter pauses only remove idle time; they cannot bypass the AH's
-- throttled-message gate. Keep a fallback if the realm starts dropping requests.
local SPEED_PAUSE = {fast = 0.35, normal = 2.5, safe = 6}
Aux.stats = {rounds = 0, rows = 0, priorities = 0, pages = 0, firstPage = 0, browse = 0, verify = 0}
local pagesThisRound, firstPageAt = 0, nil

function Aux.SetSpeed(speed)
    speed = string.lower(tostring(speed or ''))
    local pause = SPEED_PAUSE[speed]
    if not pause then return false end
    NS.Settings().speed = speed
    if Aux.env then Aux.env.ROUND_PAUSE = pause end
    return true
end

function Aux.Speed()
    local mode = NS.Settings().speed
    return SPEED_PAUSE[mode] and mode or 'fast'
end

function Aux.StatsLine()
    local a = Aux.stats
    if a.rounds == 0 then return 'no finished rounds yet (speed ' .. Aux.Speed() .. ')' end
    return string.format('round %d: %d items, browse %.1fs (first page %.1fs, %d events), candidate checks %.1fs, %d vendor priorities, speed %s',
        a.rounds, a.rows, a.browse, a.firstPage, a.pages, a.verify, a.priorities, Aux.Speed())
end

-- Return a new array, leaving the game's browse-result table unmodified.
-- The existing Sniper still does every live auction verification; the ranking
-- merely puts the best NEW below-vendor candidates first after the browse ends.
-- A browse minimum may be a BID, so this never authorises a purchase.
function Aux.RankVendorCandidates(results, known, keyString, budget)
    if type(results) ~= 'table' or type(keyString) ~= 'function' then return results, 0 end
    local getInfo = NS.GetItemInfo
    if not getInfo then return results, 0 end
    budget = budget or (GetMoney() - (NS.Settings().reserve or 0))
    if budget <= 0 then return results, 0 end
    local priority, rest = {}, {}
    for i = 1, #results do
        local row = results[i]
        local key = row and row.itemKey
        local id = key and key.itemID
        local price = row and row.minPrice or 0
        local quantity = row and row.totalQuantity or 0
        local score
        if id and price > 0 and quantity > 0 and price <= budget
            and (not known or known[keyString(key)] ~= price) then
            local ok, _, _, quality, _, _, _, _, _, _, _, vendor = pcall(getInfo, id)
            if ok and quality and quality > 0 and type(vendor) == 'number' and vendor > price then
                score = (vendor - price) * math.min(quantity, 8)
            end
        end
        if score and score > 0 then
            priority[#priority + 1] = {row = row, score = score, index = i}
        else
            rest[#rest + 1] = row
        end
    end
    if #priority == 0 then return results, 0 end
    table.sort(priority, function(a, b)
        if a.score == b.score then return a.index < b.index end
        return a.score > b.score
    end)
    local ordered = {}
    for _, record in ipairs(priority) do ordered[#ordered + 1] = record.row end
    for _, row in ipairs(rest) do ordered[#ordered + 1] = row end
    return ordered, #priority
end

local function findSniperEnv()
    local root = _G.aux_frame
    if not root or type(getfenv) ~= 'function' then return nil end
    for _, child in ipairs({root:GetChildren()}) do
        local script = child:GetScript('OnUpdate')
        if type(script) == 'function' then
            local ok, env = pcall(getfenv, script)
            if ok and type(env) == 'table' and rawget(env, 'check_list') and rawget(env, 'deals')
                and rawget(env, 'listing') and rawget(env, 'require') then
                return env
            end
        end
    end
    return nil
end

local function sniperTabIndex(module)
    local ok, env = pcall(getfenv, module.set_tab)
    local tabs = ok and type(env) == 'table' and rawget(env, 'tab_info')
    if type(tabs) == 'table' then
        for i, tab in ipairs(tabs) do
            if tab.name == SNIPER_TAB_NAME then return i end
        end
    end
    return SNIPER_TAB_FALLBACK
end

-- Reading each round's item list feeds the market recorder. The plan's buy limits
-- join aux's own deal rule: item_facts always runs just before judge, so it can
-- pass along which item is being judged.
local function installHooks(env)
    local checkList = env.check_list
    Aux.checkItems = checkList

    local startRound = env.start_round
    env.start_round = function(...)
        Aux.stats.roundAt = GetTime()
        pagesThisRound, firstPageAt = 0, nil
        return startRound(...)
    end
    env.ROUND_PAUSE = SPEED_PAUSE[Aux.Speed()]

    -- Observe browse events without making ANY extra AH API calls. Distinguish
    -- the first-page wait from pagination and subsequent item verification.
    local observer = CreateFrame('Frame')
    NS.Listen(observer, 'AUCTION_HOUSE_BROWSE_RESULTS_UPDATED')
    NS.Listen(observer, 'AUCTION_HOUSE_BROWSE_RESULTS_ADDED')
    observer:SetScript('OnEvent', function()
        if env.active and not env.checking then
            pagesThisRound = pagesThisRound + 1
            if not firstPageAt then firstPageAt = GetTime() end
        end
    end)
    Aux.browseObserver = observer

    env.check_list = function(results, seen)
        local t0 = GetTime()
        local started = Aux.stats.roundAt or t0
        local browseSeconds = math.max(0, t0 - started)
        local ok, err = pcall(NS.Market.OnBrowse, results)
        if not ok then NS.Log('market error: ' .. tostring(err)) end
        local ranked, count = Aux.RankVendorCandidates(results, env.known,
            Aux.scan.item_key_string, GetMoney() - (NS.Settings().reserve or 0))
        local response = checkList(ranked, seen)
        local stats = Aux.stats
        stats.rounds = stats.rounds + 1
        stats.rows = #results
        stats.priorities = count
        stats.pages = pagesThisRound
        stats.firstPage = firstPageAt and firstPageAt - started or browseSeconds
        stats.browse = browseSeconds
        stats.verify = math.max(0, GetTime() - t0)
        if stats.rounds % 5 == 0 then NS.Log('sniper timing ' .. Aux.StatsLine()) end
        return response
    end

    local itemFacts, judge = env.item_facts, env.judge
    local judging
    env.item_facts = function(key, itemID)
        judging = NS.Plan.For(itemID)
        return itemFacts(key, itemID)
    end
    env.judge = function(price, usual, vendor, days, percent, minProfit)
        local reason, profit, pct = judge(price, usual, vendor, days, percent, minProfit)
        if reason then return reason, profit, pct end
        local entry = judging
        if entry and price and price > 0 and price <= entry.l then
            local target = entry.t > 0 and entry.t or entry.l
            local reference = entry.f > 0 and entry.f or target
            return 'plan', math.max(1, math.floor(target * 0.95) - price), math.floor(100 * price / reference + 0.5)
        end
    end
end

function Aux.Attach()
    if Aux.env then return true end
    local env = findSniperEnv()
    if not env then return false end
    local ok, err = pcall(function()
        Aux.module = env.require('aux')
        Aux.buyBar = env.require('aux.gui.buy_bar')
        Aux.scan = env.require('aux.core.scan')
        Aux.tabIndex = sniperTabIndex(Aux.module)
        installHooks(env)
    end)
    if not ok then
        NS.Log('aux attach failed: ' .. tostring(err))
        return false
    end
    Aux.env = env
    NS.Log('attached to auxForever Sniper, tab ' .. tostring(Aux.tabIndex))
    return true
end

function Aux.IsReady()
    return Aux.env ~= nil
end

function Aux.IsRunning()
    return Aux.env ~= nil and rawget(Aux.env, 'running') == true
end

function Aux.IsTabShown()
    local frame = Aux.env and rawget(Aux.env, 'frame')
    return frame and frame:IsShown() or false
end

-- Opens the Sniper tab and presses Start, as the player would.
function Aux.StartSession()
    if not Aux.Attach() then
        NS.Log('auxForever Sniper not found; scanning needs it')
        return
    end
    if not NS.Settings().auto then return end
    if not Aux.IsTabShown() then Aux.module.set_tab(Aux.tabIndex) end
    if not Aux.IsRunning() then Aux.env.start() end
    NS.Link.MarkState()
end

function Aux.PlanChanged()
    if Aux.env then
        local settingsChanged = rawget(Aux.env, 'settings_changed')
        if settingsChanged then settingsChanged() end
    end
end

-- Looks at one item right away instead of waiting for the next round, so the next
-- price tier shows up within a second of the last one being bought. This goes round
-- the market recorder: a list of one item must not read as every other item selling out.
function Aux.RecheckItem(name)
    if not Aux.env or not name or Aux.scan.is_scanning() then return end
    Aux.scan.start{
        type = 'list',
        quiet = true,
        queries = {{blizzard_query = {name = name, exact = true}}},
        on_item_list = function(results)
            Aux.checkItems(results, {})
        end,
    }
end
