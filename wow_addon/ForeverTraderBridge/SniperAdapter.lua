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
Aux.stats = {rounds = 0, browses = 0, started = 0, rows = 0, priorities = 0, pages = 0, firstPage = 0, browse = 0, verify = 0}
local pagesThisRound, firstPageAt = 0, nil

-- OPT-IN incremental browse interception. The browse cache is cumulative.
-- Interleave at most 1 high-value vendor verification between browse-page
-- requests, never at the same time as another auction request.
-- The server throttle remains entirely owned by aux.core.scan.request().
local EARLY_PER_PAGE = 1
local EARLY_PER_ROUND = 4
local EARLY_MIN_PROFIT = 1   -- respect auxForever's actual per-item minimum
local EARLY_RECHECK_SECONDS = 35
local earlySeen = {}         -- this round: item key -> last browse minPrice
local earlyRecent = {}       -- across rounds: item key -> price/quantity/time
local earlyInProgress = false
local earlySessionDisabled = false

Aux.stats.earlyChecks = 0
Aux.stats.earlyDeals = 0
Aux.stats.earlyMisses = 0
Aux.stats.earlyPages = 0
Aux.stats.earlySpent = 0
Aux.stats.earlyFirst = 0
Aux.stats.lastEarlyFirst = 0  -- persist last result across subsequent rounds
Aux.stats.earlyRoundChecks = 0

function Aux.PagesOn()
    return NS.Settings().pages ~= false and not earlySessionDisabled
end

function Aux.SetPages(on)
    NS.Settings().pages = on == true
    return Aux.PagesOn()
end

-- Numeric fields only; no gear auction is purchased on browse data alone.
local function earlyPotential(row, now)
    local key = row and row.itemKey
    if not key or type(key.itemID) ~= 'number' then return end
    local price, qty = row.minPrice or 0, row.totalQuantity or 0
    if price <= 0 or qty <= 0 then return end
    local budget = GetMoney() - (NS.Plan.reserve or NS.Settings().reserve or 0)
    if budget <= 0 or price > budget then return end
    local id = key.itemID
    local keyStr = Aux.scan.item_key_string(key)
    if earlySeen[keyStr] == price then return end
    local prev = earlyRecent[keyStr]
    if prev and prev.price == price and prev.qty == qty
        and now - prev.at < EARLY_RECHECK_SECONDS then return end
    local name, quality, vendor
    local ok, a, _, c, _, _, _, _, _, _, _, v = pcall(NS.GetItemInfo, id)
    if ok then name, quality, vendor = a, c, v end
    if not name or not quality or quality <= 0 or not vendor or vendor <= price then return end
    local profit = vendor - price
    local minProfit = math.max(EARLY_MIN_PROFIT, tonumber(Aux.module.account_data.sniper_profit) or 0)
    if profit < minProfit then return end
    if Aux.module.account_data.sniper_ignored
        and Aux.module.account_data.sniper_ignored[keyStr] then return end
    -- Page minima can be bids; a live item search decides if buyout exists.
    local score = profit * math.min(qty, 8)
    return {row = row, keyStr = keyStr, score = score, profit = profit, price = price, qty = qty}
end

function Aux.PreviewBrowsePage()
    if not Aux.PagesOn() or earlyInProgress or not Aux.env
        or not Aux.env.active or Aux.env.checking
        or Aux.stats.earlyRoundChecks >= EARLY_PER_ROUND then return end
    if NS.Snipe and NS.Snipe.state and NS.Snipe.state.phase ~= 0 then return end
    -- No gain from interleaving searches if the whole browse is already loaded.
    if C_AuctionHouse.HasFullBrowseResults() then return end
    local rows = C_AuctionHouse.GetBrowseResults()
    if type(rows) ~= 'table' or #rows == 0 then return end
    local now = GetTime()
    local choices = {}
    for _, row in ipairs(rows) do
        local result = earlyPotential(row, now)
        if result then choices[#choices + 1] = result end
    end
    if #choices == 0 then return end
    table.sort(choices, function(a, b)
        if a.score == b.score then return a.profit > b.profit end
        return a.score > b.score
    end)
    Aux.stats.earlyPages = Aux.stats.earlyPages + 1
    local before = #rows
    local checks = math.min(EARLY_PER_PAGE, EARLY_PER_ROUND - Aux.stats.earlyRoundChecks, #choices)
    earlyInProgress = true
    for i = 1, checks do
        local c = choices[i]
        -- Mark before the yielding search. If it is interrupted by a user
        -- purchase, the candidate cannot be reissued from this same page.
        earlySeen[c.keyStr] = c.price
        earlyRecent[c.keyStr] = {price = c.price, qty = c.qty, at = now}
        Aux.stats.earlyRoundChecks = Aux.stats.earlyRoundChecks + 1
        Aux.stats.earlyChecks = Aux.stats.earlyChecks + 1
        if not Aux.stats.earlyFirst or Aux.stats.earlyFirst == 0 then
            Aux.stats.earlyFirst = now - (Aux.stats.roundAt or now)
            Aux.stats.lastEarlyFirst = Aux.stats.earlyFirst
        end
        -- The Sniper replaces an existing record in place when the same
        -- item key is rechecked, so #deals may stay constant on success.
        local beforeDeal
        for _, deal in ipairs(Aux.env.deals) do
            if deal.deal_key == c.keyStr then beforeDeal = deal; break end
        end
        local t0 = GetTime()
        -- MUST NOT call through pcall: scan.read_item yields on Lua 5.1.
        Aux.env.check_item(c.row.itemKey, c.keyStr, c.price)
        Aux.stats.earlySpent = Aux.stats.earlySpent + GetTime() - t0
        local newDeal
        for _, deal in ipairs(Aux.env.deals) do
            if deal.deal_key == c.keyStr then newDeal = deal; break end
        end
        if newDeal and newDeal ~= beforeDeal and not newDeal.deal_gone
            and not newDeal.deal_bought then
            Aux.stats.earlyDeals = Aux.stats.earlyDeals + 1
            NS.Log('early page bargain: ' .. tostring(c.keyStr) ..
                ' +at least ' .. tostring(c.profit) .. 'c per unit')
            break -- an actionable deal takes precedence over pagination
        else
            Aux.stats.earlyMisses = Aux.stats.earlyMisses + 1
        end
    end
    earlyInProgress = false
    -- If the client replaced the browse cache while searching, bail out of
    -- this round; never let a partial browse mark other auctions as gone.
    local after = C_AuctionHouse.GetBrowseResults()
    if type(after) ~= 'table' or #after < before or C_AuctionHouse.HasFullBrowseResults() then
        earlySessionDisabled = true
        NS.Log('incremental browse cache changed during verification; disabled pages for this session')
        Aux.scan.abort()
        Aux.module.coro_wait()
    end
end

function Aux.PreviewStats()
    local s = Aux.stats
    local minProfit = math.max(EARLY_MIN_PROFIT, tonumber(Aux.module and Aux.module.account_data.sniper_profit) or 0)
    return string.format('pages %s%s, min %dc, checks %d (this round %d), verified %d, unverified %d, search %.1fs, last first check %.1fs',
        Aux.PagesOn() and 'on' or 'off', earlySessionDisabled and ' (fallback)' or '', minProfit,
        s.earlyChecks, s.earlyRoundChecks, s.earlyDeals, s.earlyMisses,
        s.earlySpent, s.lastEarlyFirst)
end


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
    local a, env = Aux.stats, Aux.env
    local speed = Aux.Speed()
    local finished = env and tonumber(env.round) or 0
    local phase = 'idle'
    if env and env.active then
        local checking = env.checking
        if checking and checking.total then
            phase = string.format('checking candidates %d/%d', checking.done or 0, checking.total)
        else
            phase = 'reading browse pages'
        end
    end
    local elapsed = a.roundAt and math.max(0, GetTime() - a.roundAt) or 0
    if a.browses == 0 then
        return string.format('browse not returned yet; %s, %.1fs elapsed, pages %d, speed %s',
            phase, elapsed, pagesThisRound, speed)
    end
    return string.format('browses %d, rounds %d, %d items, browse %.1fs (page 1 %.1fs, events %d), verification %s, priority %d, early %d/%d, current %s %.1fs, speed %s',
        a.browses, finished, a.rows, a.browse, a.firstPage, a.pages,
        env and env.active and env.checking and 'active' or string.format('last %.1fs', a.verify),
        a.priorities, a.earlyDeals, a.earlyChecks, phase, elapsed, speed)
end

-- auxForever groups listings by search_signature, which can represent multiple
-- *different enchanted items* at the same price. Preserve the original signature,
-- but give every distinct gear auction its own Sniper row so selecting "of Magic"
-- cannot silently select "of Power". This changes only the Sniper's visible row
-- grouping, never the auction key or the item's price.
function Aux.UniqueGearSignatures(records)
    if type(records) ~= 'table' then return end
    for _, record in ipairs(records) do
        if record and not record.commodity and record.search_signature then
            if not record.ft_base_signature then
                record.ft_base_signature = record.search_signature
            end
            local identity = record.auction_id or record.name or record.item_key or '?'
            record.search_signature = record.ft_base_signature .. '|FTGEAR|' .. tostring(identity)
        end
    end
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

    local originalSetDatabase = env.listing.SetDatabase
    env.listing.SetDatabase = function(self, records)
        Aux.UniqueGearSignatures(records)
        return originalSetDatabase(self, records)
    end

    -- aux.core.scan is a single coroutine. Its request helper has completed
    -- waiting for a page event before we execute the early verification.
    -- Any nested read_item() searches share that same request/throttle system.
    local scanEnvironment = getfenv(Aux.scan.start)
    local originalRequest = rawget(scanEnvironment, 'request')
    if type(originalRequest) == 'function' then
        scanEnvironment.request = function(send, events, match, cached)
            local answer = originalRequest(send, events, match, cached)
            if not earlyInProgress and type(events) == 'table' then
                for _, event in ipairs(events) do
                    if event == 'AUCTION_HOUSE_BROWSE_RESULTS_UPDATED' or
                        event == 'AUCTION_HOUSE_BROWSE_RESULTS_ADDED' then
                        if not answer or answer == 'AUCTION_HOUSE_BROWSE_FAILURE' then
                            if Aux.PagesOn() and Aux.stats.earlyRoundChecks > 0 and env.active then
                                earlySessionDisabled = true
                                NS.Log('browse pagination failed after early search: reverting to legacy')
                                Aux.scan.abort()
                                Aux.module.coro_wait()
                            end
                        elseif answer then
                            Aux.PreviewBrowsePage()
                        end
                        break
                    end
                end
            end
            return answer
        end
    else
        NS.Log('incremental browse disabled: aux scan request helper not found')
        earlySessionDisabled = true
    end

    local startRound = env.start_round
    env.start_round = function(...)
        Aux.stats.roundAt = GetTime()
        Aux.stats.started = Aux.stats.started + 1
        Aux.stats.earlyRoundChecks = 0
        Aux.stats.earlyFirst = 0
        wipe(earlySeen)
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
        local stats = Aux.stats
        -- Increment HERE, before checking candidates. auxForever yields for
        -- every candidate and the Confirm button may abort this coroutine.
        -- The old code incremented afterwards, reporting zero despite 20+ browses.
        stats.browses = stats.browses + 1
        stats.rows = #results
        stats.pages = pagesThisRound
        stats.firstPage = firstPageAt and firstPageAt - started or browseSeconds
        stats.browse = browseSeconds
        stats.verify = 0
        local ok, err = pcall(NS.Market.OnBrowse, results)
        if not ok then NS.Log('market error: ' .. tostring(err)) end
        local ranked, count = Aux.RankVendorCandidates(results, env.known,
            Aux.scan.item_key_string, GetMoney() - (NS.Settings().reserve or 0))
        stats.priorities = count
        -- Yielding scan: it can be interrupted intentionally by a purchase.
        -- stats.browses remains accurate even if control never returns here.
        local response = checkList(ranked, seen)
        stats.rounds = stats.rounds + 1
        stats.verify = math.max(0, GetTime() - t0)
        if stats.browses % 5 == 0 then NS.Log('sniper timing ' .. Aux.StatsLine()) end
        return response
    end

    local itemFacts, judge = env.item_facts, env.judge
    local judging
    env.item_facts = function(key, itemID)
        judging = NS.Plan.For(itemID)
        return itemFacts(key, itemID)
    end
    -- "bets off" previously filtered only the Confirm button, while auxForever
    -- still opened every usual-price/plan candidate. Each live item check costs
    -- another server request (14.4s on a five-priority browse). In vendor-only
    -- mode, skip those checks BEFORE opening auctions, and reject unaffordable
    -- minima. The cheapest browse price is not necessarily a buyout, so aux's
    -- normal live recheck remains mandatory for every approved candidate.
    env.judge = function(price, usual, vendor, days, percent, minProfit)
        local reason, profit, pct = judge(price, usual, vendor, days, percent, minProfit)
        if not NS.Settings().bets then
            if reason ~= 'vendor' then return nil end
            local free = GetMoney() - (NS.Plan.reserve or NS.Settings().reserve or 0)
            if not price or price > free then return nil end
            return reason, profit, pct
        end
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
