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
local EARLY_NO_DEAL_RECHECK_SECONDS = 120
local EARLY_SLOW_SECONDS = 8
local earlySeen = {}         -- this round: item key -> last browse minPrice
local earlyRecent = {}       -- across rounds: item key -> price/result/time
local earlyInProgress = false
local earlyCachedOnly = false
local earlySessionDisabled = false
-- The live export contained 22 failed Training Sword attempts at the same
-- price in about three minutes. A known-dead browse fingerprint should not
-- monopolise the highest-priority item query on every fresh browse.
-- Quarantine only confirmed disappearances, never throttling or timeouts.
local goneCache = {}
local GONE_DELAYS = {25, 60, 180}
-- Multiple enchanted variants of one item can share the same dead cheapest
-- browse price. Quarantine item+price, not just the specific item key.
local itemGoneCache = {}
local ITEM_GONE_DELAYS = {45, 120, 300}

function Aux.DeadCacheOn()
    return NS.Settings().deadcache ~= false
end

function Aux.SetDeadCache(on)
    NS.Settings().deadcache = on == true
    if not on then
        wipe(goneCache)
        wipe(itemGoneCache)
        if NS.Snipe and NS.Snipe.ClearGoneSkips then NS.Snipe.ClearGoneSkips() end
    end
    return Aux.DeadCacheOn()
end
Aux.stats.goneReports = 0
Aux.stats.goneFilteredEarly = 0
Aux.stats.goneFilteredFull = 0
Aux.stats.goneChanged = 0
Aux.stats.browseFailures = 0
Aux.stats.safetyStops = 0
Aux.stats.lastBrowseFailureAt = nil
Aux.stats.cachedRefreshSkips = 0
Aux.stats.prepareExpirations = 0
Aux.stats.uncertainHolds = 0
-- A freshly selected deal can still return old local AH rows without an
-- ITEM_SEARCH_RESULTS_UPDATED event. Such a result is not a live quote.
local cachedRefresh = setmetatable({}, {__mode='k'})
function Aux.ClearCachedRefresh(deal)
    if deal then cachedRefresh[deal] = nil end
end
function Aux.IsCachedRefresh(deal)
    return deal and cachedRefresh[deal] == true or false
end
local safetyStopScheduled = false

-- The realm may keep accepting new scan rounds even when its AH has stopped
-- answering. Never spin another full browse automatically after a 20-second
-- request timeout. The player can manually restart the Sniper when it recovers.
function Aux.StopOnBrowseFailure(env, reason)
    if not env or not rawget(env, 'active') or not rawget(env, 'running')
        or safetyStopScheduled then return end
    safetyStopScheduled = true
    Aux.stats.browseFailures = Aux.stats.browseFailures + 1
    Aux.stats.lastBrowseFailureAt = GetTime()
    NS.Log('auction-house browse failed (' .. tostring(reason) .. '): stopping Sniper to avoid repeat timeouts')
    -- Defer: stopping inside the scan's coroutine kills the coroutine at its
    -- next yield. Let the original request unwind first.
    C_Timer.After(0, function()
        safetyStopScheduled = false
        if Aux.env == env and rawget(env, 'running') and type(env.stop) == 'function' then
            env.stop()
            Aux.stats.safetyStops = Aux.stats.safetyStops + 1
            print('|cff66ccffForever Trader|r Auction house did not answer. Sniper stopped automatically; restart it manually after the server recovers.')
            NS.Log('sniper safety stop complete')
        end
    end)
end

function Aux.IsGoneCooldown(key, price, qty)
    if not Aux.DeadCacheOn() then return false end
    key = tostring(key or '')
    price = tonumber(price)
    local now = GetTime()
    local itemID = tonumber(string.match(key, '^(%d+)'))
    if itemID and price and price > 0 then
        local itemPriceKey = tostring(itemID) .. ':' .. tostring(price)
        local group = itemGoneCache[itemPriceKey]
        if group then
            if now < group.untilTime then return true end
            if now - group.reportedAt > 600 then itemGoneCache[itemPriceKey] = nil end
        end
    end
    local g = goneCache[key]
    if not g then return false end
    -- Changing totalQuantity can mean unrelated enchants were posted; it does
    -- NOT prove the same cheapest bid is now a buyable auction.
    if price and price ~= g.price then
        goneCache[key] = nil
        if Aux.env and Aux.env.known then Aux.env.known[key] = nil end
        Aux.stats.goneChanged = Aux.stats.goneChanged + 1
        return false
    end
    if now >= g.untilTime then
        if not g.released then
            g.released = true
            if Aux.env and Aux.env.known then Aux.env.known[key] = nil end
        end
        if now - g.reportedAt > 600 then goneCache[key] = nil end
        return false
    end
    return true
end

function Aux.ReportUnavailable(deal)
    if not Aux.DeadCacheOn() or not deal then return 0 end
    local key = tostring(deal.deal_key or deal.item_id or '')
    if key == '' then return 0 end
    -- Gear uses buyout_price, whereas commodities use unit_buyout_price.
    -- A missing per-page observation must not silently disable protection.
    local price = tonumber(deal.ft_browse_price)
    if not price or price <= 0 then
        price = tonumber(deal.buyout_price or deal.unit_buyout_price)
    end
    if not price or price <= 0 then return 0 end
    local now = GetTime()
    local prev = goneCache[key]
    local same = prev and prev.price == price and now - prev.reportedAt <= 600
    local strikes = same and math.min(prev.strikes + 1, #GONE_DELAYS) or 1
    local duration = GONE_DELAYS[strikes]
    goneCache[key] = {price=price, untilTime=now+duration,
        reportedAt=now, strikes=strikes}

    local itemID = tonumber(deal.item_id) or tonumber(string.match(key, '^(%d+)'))
    if itemID then
        local itemPriceKey = tostring(itemID) .. ':' .. tostring(price)
        local previous = itemGoneCache[itemPriceKey]
        local repeated = previous and now - previous.reportedAt <= 600
        local itemStrikes = repeated and math.min(previous.strikes + 1, #ITEM_GONE_DELAYS) or 1
        local itemDuration = ITEM_GONE_DELAYS[itemStrikes]
        itemGoneCache[itemPriceKey] = {untilTime=now+itemDuration,
            reportedAt=now, strikes=itemStrikes}
        NS.Log('unavailable ' .. key .. ' at ' .. price ..
            'c: key paused ' .. duration .. 's, item+price paused ' .. itemDuration .. 's')
    else
        NS.Log('unavailable ' .. key .. ' at ' .. price .. 'c: key paused ' .. duration .. 's')
    end
    Aux.stats.goneReports = Aux.stats.goneReports + 1
    return duration
end

function Aux.GoneStats()
    local n, now = 0, GetTime()
    for _, g in pairs(goneCache) do
        if now < g.untilTime then n = n + 1 end
    end
    return n
end

function Aux.FaultStats()
    return string.format('browse failures %d, safety stops %d, last failure %s, stale refresh skips %d, slow preparations %d, uncertain purchase holds %d',
        Aux.stats.browseFailures, Aux.stats.safetyStops,
        Aux.stats.lastBrowseFailureAt and
            string.format('%.1fs ago', math.max(0, GetTime() - Aux.stats.lastBrowseFailureAt)) or 'never',
        Aux.stats.cachedRefreshSkips, Aux.stats.prepareExpirations, Aux.stats.uncertainHolds)
end

function Aux.ItemGoneStats()
    local n, now = 0, GetTime()
    for _, g in pairs(itemGoneCache) do
        if now < g.untilTime then n = n + 1 end
    end
    return n
end


Aux.stats.earlyChecks = 0
Aux.stats.earlyDeals = 0
Aux.stats.earlyMisses = 0
Aux.stats.earlyPages = 0
Aux.stats.earlySpent = 0
Aux.stats.earlyFirst = 0
Aux.stats.lastEarlyFirst = 0  -- persist last result across subsequent rounds
Aux.stats.earlyRoundChecks = 0
Aux.stats.earlyRepeatSkips = 0
Aux.stats.earlySlowFallbacks = 0
Aux.stats.earlySlowSeconds = 0
Aux.stats.earlyVerifiedNoDeal = 0
Aux.stats.earlyCachedFallbacks = 0
Aux.stats.cachePruned = 0

-- The browse quantity covers *all* auctions for an item, including other
-- enchantments and bids. Quantity churn alone is not proof a cheap BUYOUT
-- appeared. A changed minimum price is always eligible immediately.
function Aux.IsRecentEarly(key, price, now)
    local prev = earlyRecent[tostring(key or '')]
    if not prev or prev.price ~= tonumber(price) then return false end
    local seconds = prev.noDeal and EARLY_NO_DEAL_RECHECK_SECONDS or EARLY_RECHECK_SECONDS
    return (now or GetTime()) - prev.at < seconds
end

function Aux.NoteEarlyCheck(key, price, noDeal, now)
    earlyRecent[tostring(key or '')] = {
        price = tonumber(price), at = now or GetTime(), noDeal = noDeal == true,
    }
end

-- A successful buy proves the price was real, and more units may be available.
-- Let the normal Sniper inspect another offer at the same price immediately.
function Aux.OnPurchased(deal)
    if not deal then return end
    local key = tostring(deal.deal_key or '')
    if key ~= '' then
        earlyRecent[key] = nil
        goneCache[key] = nil
        if Aux.env and Aux.env.known then Aux.env.known[key] = nil end
    end
    local id = tonumber(deal.item_id)
    local price = tonumber(deal.ft_browse_price or deal.buyout_price or deal.unit_buyout_price)
    if id and price and price > 0 then
        itemGoneCache[tostring(id) .. ':' .. tostring(price)] = nil
    end
end

local nextPruneAt = 120
function Aux.PruneCaches(now)
    now = now or GetTime()
    if now < nextPruneAt then return end
    nextPruneAt = now + 120
    for key, old in pairs(earlyRecent) do
        if now - old.at > 300 then
            earlyRecent[key] = nil
            Aux.stats.cachePruned = Aux.stats.cachePruned + 1
        end
    end
    for key, old in pairs(goneCache) do
        if now - old.reportedAt > 900 then
            goneCache[key] = nil
            Aux.stats.cachePruned = Aux.stats.cachePruned + 1
        end
    end
    for key, old in pairs(itemGoneCache) do
        if now - old.reportedAt > 900 then
            itemGoneCache[key] = nil
            Aux.stats.cachePruned = Aux.stats.cachePruned + 1
        end
    end
end

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
    if Aux.IsGoneCooldown(keyStr, price, qty) then
        Aux.stats.goneFilteredEarly = Aux.stats.goneFilteredEarly + 1
        return
    end
    if earlySeen[keyStr] == price then return end
    if Aux.IsRecentEarly(keyStr, price, now) then
        Aux.stats.earlyRepeatSkips = Aux.stats.earlyRepeatSkips + 1
        return
    end
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
        Aux.NoteEarlyCheck(c.keyStr, c.price, false, now)
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
        earlyCachedOnly = false
        -- MUST NOT call through pcall: scan.read_item yields on Lua 5.1.
        Aux.env.check_item(c.row.itemKey, c.keyStr, c.price)
        local spent = math.max(0, GetTime() - t0)
        Aux.stats.earlySpent = Aux.stats.earlySpent + spent
        -- A slow item query is a bad time to interrupt the browse between
        -- pages. Fall back to aux's ordinary scan for the rest of the session,
        -- without cancelling a valid deal found by this completed query.
        if spent >= EARLY_SLOW_SECONDS and not earlySessionDisabled then
            earlySessionDisabled = true
            Aux.stats.earlySlowFallbacks = Aux.stats.earlySlowFallbacks + 1
            Aux.stats.earlySlowSeconds = spent
            NS.Log(string.format('early checks disabled for session: one live verification took %.1fs (limit %ds)',
                spent, EARLY_SLOW_SECONDS))
        end
        local newDeal
        for _, deal in ipairs(Aux.env.deals) do
            if deal.deal_key == c.keyStr then newDeal = deal; break end
        end
        if earlyCachedOnly then
            -- aux's 1-second fallback returns 'true' for local cached results,
            -- *not* a server event. On a slow AH this is often the stale 1s
            -- bid that produced a flood of UNAVAILABLE cursor offers.
            -- Don't promote such a result as a fresh early bargain.
            Aux.stats.earlyCachedFallbacks = Aux.stats.earlyCachedFallbacks + 1
            earlySessionDisabled = true
            if newDeal and newDeal ~= beforeDeal then newDeal.deal_gone = true end
            if Aux.env.known then Aux.env.known[c.keyStr] = nil end
            NS.Log('early page suspended: item query used cached-only data instead of a fresh server event')
        end
        if not earlyCachedOnly and newDeal and newDeal ~= beforeDeal and not newDeal.deal_gone
            and not newDeal.deal_bought then
            -- Attach monotonic timing evidence directly to the deal picked up
            -- by Snipe.lua. Never infer these timings from the wall-clock
            -- "found 2m ago" label.
            newDeal.ft_first_page_at = firstPageAt or now
            newDeal.ft_verified_at = GetTime()
            newDeal.ft_incremental = true
            newDeal.ft_browse_price = c.price
            newDeal.ft_browse_qty = c.qty
            Aux.stats.earlyDeals = Aux.stats.earlyDeals + 1
            NS.Log('early page bargain: ' .. tostring(c.keyStr) ..
                ' +at least ' .. tostring(c.profit) .. 'c per unit')
            break -- an actionable deal takes precedence over pagination
        else
            Aux.stats.earlyMisses = Aux.stats.earlyMisses + 1
            -- aux marks the price as known ONLY when its item search answered.
            -- Do not apply a long negative cache to timeouts or dropped queries.
            if not earlyCachedOnly and Aux.env.known and Aux.env.known[c.keyStr] == c.price then
                Aux.NoteEarlyCheck(c.keyStr, c.price, true, GetTime())
                Aux.stats.earlyVerifiedNoDeal = Aux.stats.earlyVerifiedNoDeal + 1
            end
        end
        if earlySessionDisabled then break end
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
    return string.format('pages %s%s, deadcache %s, min %dc, checks %d (this round %d), verified %d, unverified %d, search %.1fs, first %.1fs, dead %d (item-prices %d, early skips %d, full skips %d), repeat skips %d, verified no-deal %d, slow fallbacks %d (%.1fs), cached-only fallbacks %d',
        Aux.PagesOn() and 'on' or 'off', earlySessionDisabled and ' (fallback)' or '',
        Aux.DeadCacheOn() and 'on' or 'off', minProfit,
        s.earlyChecks, s.earlyRoundChecks, s.earlyDeals, s.earlyMisses,
        s.earlySpent, s.lastEarlyFirst, Aux.GoneStats(),
        Aux.ItemGoneStats(), s.goneFilteredEarly, s.goneFilteredFull,
        s.earlyRepeatSkips, s.earlyVerifiedNoDeal, s.earlySlowFallbacks, s.earlySlowSeconds,
        s.earlyCachedFallbacks)
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
            local refreshingDeal = rawget(env, 'refreshing')
            local answer = originalRequest(send, events, match, cached)
            if answer == true and refreshingDeal then
                -- true is aux's cached-results fallback, not a matching AH
                -- update event. Never promote it to a Buy-ready cursor offer.
                cachedRefresh[refreshingDeal] = true
                NS.Log('cached-only selected-deal verification: ' .. tostring(refreshingDeal.name))
            end
            if type(events) == 'table' then
                if earlyInProgress and answer == true then
                    for _, event in ipairs(events) do
                        if event == 'ITEM_SEARCH_RESULTS_UPDATED' or
                            event == 'ITEM_SEARCH_RESULTS_ADDED' or
                            event == 'COMMODITY_SEARCH_RESULTS_UPDATED' or
                            event == 'COMMODITY_SEARCH_RESULTS_ADDED' then
                            earlyCachedOnly = true
                            break
                        end
                    end
                end
                for _, event in ipairs(events) do
                    if event == 'AUCTION_HOUSE_BROWSE_RESULTS_UPDATED' or
                        event == 'AUCTION_HOUSE_BROWSE_RESULTS_ADDED' then
                        if not answer or answer == 'AUCTION_HOUSE_BROWSE_FAILURE' then
                            -- This path also runs with /ft pages off: the
                            -- underlying aux Sniper still scans indefinitely.
                            Aux.StopOnBrowseFailure(env, answer or 'no answer')
                            if not earlyInProgress and Aux.PagesOn()
                                and Aux.stats.earlyRoundChecks > 0 and env.active then
                                earlySessionDisabled = true
                                NS.Log('browse pagination failed after early search: reverting to legacy')
                            end
                        elseif not earlyInProgress then
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
        Aux.PruneCaches(GetTime())
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
        -- Market.OnBrowse receives every row; only Sniper's candidate set
        -- is filtered. This prevents repeated unbuyable candidates from
        -- spending live AH item-search requests and blocking better bargains.
        local eligible = {}
        for _, row in ipairs(results) do
            local key = row.itemKey and Aux.scan.item_key_string(row.itemKey)
            if key and Aux.IsGoneCooldown(key, row.minPrice, row.totalQuantity) then
                stats.goneFilteredFull = stats.goneFilteredFull + 1
            else
                eligible[#eligible + 1] = row
            end
        end
        local ranked, count = Aux.RankVendorCandidates(eligible, env.known,
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
