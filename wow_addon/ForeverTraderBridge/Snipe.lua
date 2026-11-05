-- Snipe: the Confirm button at the cursor.
--
-- Every purchase starts from one real press: a mouse click, a mouse-wheel notch or
-- a key press. Stackable items are bought all at once: the press asks the server
-- for the price of every unit under the limit, and the addon confirms when the
-- answer arrives. Gear is one press per auction, because the game requires it.
-- Nothing here ever starts a purchase by itself.
local _, NS = ...
local Snipe = {}
NS.Snipe = Snipe

local floor, ceil, min, max = math.floor, math.ceil, math.min, math.max

local PHASE = {IDLE = 0, PREPARE = 1, READY = 2, QUOTING = 3, CONFIRMING = 4, BUYING = 5, DONE = 6, LOST = 7}
Snipe.PHASE = PHASE

local ARM_DELAY = 0.2       -- a click meant for something else cannot buy
local LABEL_SETTLE = 0.12   -- aux redraws its buy bar ten times a second
local READY_TTL = 15        -- ignored deals stop holding the Sniper's rounds
local SKIP_SECONDS = 180
-- aux's live re-read first waits, without limit, for the server's request throttle, then
-- up to 20 seconds for the answer. Giving up sooner abandoned deals aux was about to offer.
local PREPARE_TIMEOUT = 25
local SLOW_SECONDS = 3
local TIMEOUT_SKIP_SECONDS = 30 -- a slow answer says nothing about the deal
local DONE_SECONDS, LOST_SECONDS = 0.8, 1.2
local CHOOSE_EVERY = 0.1
local MAX_RETRIES = 6
local MAX_SELECT_TRIES = 3
local NO_ANSWER_SECONDS = 12
local HELD_SECONDS = 24 * 60 * 60 -- a plan's unit cap counts what was bought this long ago

local S = {phase = PHASE.IDLE, retries = 0}
local skipped = {}
local nextChoose = 0
local button, titleText, reasonText, detailText

Snipe.state = S

local function coin(c)
    c = floor(c or 0)
    local g, s, k = floor(c / 10000), floor(c / 100) % 100, c % 100
    if g > 0 then return string.format('%dg %02ds %02dc', g, s, k) end
    if s > 0 then return string.format('%ds %02dc', s, k) end
    return string.format('%dc', k)
end
Snipe.Coin = coin

local PHASE_NAMES = {}
for name, value in pairs(PHASE) do PHASE_NAMES[value] = name end

local function setPhase(phase)
    if S.phase ~= phase then
        S.phase = phase
        S.phaseAt = GetTime()
        NS.Link.MarkState()
        if phase ~= PHASE.IDLE then
            NS.Log(string.format('confirm %s: %s x%d %s', PHASE_NAMES[phase], S.deal and S.deal.name or '?', S.n or 0, S.message and phase >= PHASE.DONE and S.message or ''))
        end
    end
end

local function dealKey(deal)
    return tostring(deal.deal_key) .. ':' .. tostring(ceil(deal.unit_buyout_price or 0))
end

-- Units bought on a bet, per item, so the plan's unit cap holds across purchases and
-- reloads. The engine does not know what the player holds, so the addon keeps count.
local function heldTable()
    local db = ForeverTraderBridgeDB
    if type(db) ~= 'table' then
        Snipe.heldFallback = Snipe.heldFallback or {}
        return Snipe.heldFallback
    end
    db.held = type(db.held) == 'table' and db.held or {}
    return db.held
end

function Snipe.Held(itemID)
    local entry = heldTable()[itemID]
    if type(entry) ~= 'table' then return 0 end
    if NS.Now() - (entry.ts or 0) > HELD_SECONDS then
        heldTable()[itemID] = nil
        return 0
    end
    return entry.n or 0
end

function Snipe.AddHeld(itemID, units)
    local held = Snipe.Held(itemID)
    local entry = heldTable()[itemID]
    heldTable()[itemID] = {n = held + units, ts = entry and entry.ts or NS.Now()}
end

-- Pure sizing rule, testable outside the game. Below-vendor deals may use all free
-- cash. Anything else is a bet on resale, so it gets a share of free cash and the
-- plan's unit cap, less what was already bought (held).
function Snipe.Size(deal, money, reserve, specFraction, planEntry, held)
    local free = money - reserve
    if free <= 0 then return 0, 0, 0 end
    local isVendor = deal.deal_reason == 'vendor'
    local budget = isVendor and free or floor(free * specFraction)
    local vendor = deal.deal_vendor or 0

    local function unitProfit(price)
        if isVendor then return vendor - price end
        if planEntry and planEntry.t > 0 then return floor(planEntry.t * 0.95) - price end
        return deal.deal_profit or 0
    end

    if not deal.commodity then
        local cost = deal.buyout_price or 0
        if cost <= 0 or cost > budget then return 0, 0, 0 end
        local count = deal.count or 1
        return count, cost, (deal.deal_profit or 0) * count
    end

    local cap = math.huge
    if not isVendor and planEntry and planEntry.q > 0 then
        cap = planEntry.q - (held or 0)
        if cap <= 0 then return 0, 0, 0 end
    end
    local n, cost, profit = 0, 0, 0
    for _, tier in ipairs(deal.deal_tiers or {deal}) do
        local price = ceil(tier.commodity_unit_price or tier.unit_buyout_price or 0)
        local available = tier.count or 0
        if price > 0 and available > 0 and n < cap then
            local take = min(available, cap - n, floor(budget / price))
            if take <= 0 then break end
            n = n + take
            cost = cost + take * price
            budget = budget - take * price
            profit = profit + take * unitProfit(price)
        end
    end
    return n, cost, profit
end

local function sizeNow(deal)
    local settings = NS.Settings()
    local reserve = NS.Plan.reserve or settings.reserve
    local spec = NS.Plan.spec or settings.spec
    return Snipe.Size(deal, GetMoney(), reserve, spec, NS.Plan.For(deal.item_id), Snipe.Held(deal.item_id))
end

local function chooseDeal()
    local env = NS.Aux.env
    local best, score
    local now = GetTime()
    local betsOn = NS.Settings().bets
    for _, deal in ipairs(env.deals or {}) do
        if not deal.deal_gone and not deal.deal_bought and (skipped[dealKey(deal)] or 0) <= now
            and (betsOn or deal.deal_reason == 'vendor') then
            local n, _, profit = sizeNow(deal)
            if n > 0 and (not best or profit > score) then
                best, score = deal, profit
            end
        end
    end
    return best
end

local function placeAtCursor()
    local x, y = GetCursorPosition()
    local scale = UIParent:GetEffectiveScale()
    button:ClearAllPoints()
    if x and y and scale and scale > 0 then
        button:SetPoint('CENTER', UIParent, 'BOTTOMLEFT', x / scale, y / scale)
    else
        button:SetPoint('CENTER', UIParent, 'CENTER', 0, 120)
    end
end

local function showButton(r, g, b)
    if not button:IsShown() then
        placeAtCursor()
        button:Show()
    end
    button:SetBackdropColor(r, g, b, 0.96)
end

local function hideButton()
    button:Hide()
end

local function selectDeal(deal)
    local env = NS.Aux.env
    env.update_deals()
    env.listing:SetSelectedRecord(deal)
    -- Runs aux's live re-read of the item now, so nothing stale can be bought.
    env.update_selection()
end

local function letGo()
    local env = NS.Aux.env
    if env and env.listing then
        env.listing:SetSelectedRecord()
        env.update_selection()
    end
end

local function stillListed(deal)
    for _, listed in ipairs(NS.Aux.env.deals or {}) do
        if listed == deal then return true end
    end
    return false
end

local function skip(deal, seconds)
    skipped[dealKey(deal)] = GetTime() + (seconds or SKIP_SECONDS)
end

local function begin(deal, isRetry)
    S.deal = deal
    S.qtySetAt = nil
    S.selectTries = 0
    S.failedEvent, S.boughtEvent = nil, nil
    if not isRetry then
        S.retries = 0
        S.armedAt = GetTime() + ARM_DELAY
    end
    setPhase(PHASE.PREPARE)
    selectDeal(deal)
end

local function finish(phase, message)
    S.message = message
    setPhase(phase)
end

local function finishIdle()
    S.deal, S.n, S.cost, S.profit, S.quote = nil, nil, nil, nil, nil
    setPhase(PHASE.IDLE)
end

-- Reads the item again and offers whatever is still under the limit.
local function retry(reason)
    local deal = S.deal
    S.retries = S.retries + 1
    if not deal or S.retries > MAX_RETRIES then
        finish(PHASE.LOST, reason or 'Lost')
        return
    end
    deal.deal_gone, deal.deal_bought = nil, nil
    letGo()
    begin(deal, true)
end

local function barShows(deal)
    local line1 = NS.Aux.buyBar.lines()
    return type(line1) == 'string' and string.find(line1, deal.name, 1, true) ~= nil
end

-- Why a deal could not be readied, for the log: these failures were only ever seen live.
local function describeStuck(deal)
    local env, bar = NS.Aux.env, NS.Aux.buyBar
    local listing = env.listing
    local ok, text = pcall(function()
        local sel = listing:GetSelection()
        local rec = sel and sel.record
        local refreshing = rawget(env, 'refreshing')
        local line1 = bar.lines()
        local throttle = C_AuctionHouse.IsThrottledMessageSystemReady and C_AuctionHouse.IsThrottledMessageSystemReady()
        return 'throttle_ready=' .. tostring(throttle) .. ' ' .. string.format('sel=%s same=%s sigmatch=%s selected=%s indb=%s refresh=%s busy=%s bar=%s label=%s qty=%s/%s sig=%s key=%s',
            rec and tostring(rec.name) or 'nil', tostring(rec == deal),
            tostring(rec ~= nil and rec.search_signature == deal.search_signature),
            tostring(listing.selected == deal), tostring(listing:ContainsRecord(deal) and true or false),
            refreshing == nil and 'no' or (refreshing == deal and 'this' or 'other'), tostring(bar.busy()),
            string.sub(tostring(line1), 1, 40), string.sub(tostring(bar.primary_label()), 1, 30),
            tostring(bar.other_input and bar.other_input:GetNumber()), tostring(S.n),
            tostring(deal.search_signature), tostring(deal.deal_item_key and deal.deal_key or deal.item_key))
    end)
    return ok and text or ('describe failed: ' .. tostring(text))
end

local function tickPrepare()
    local env, bar, deal = NS.Aux.env, NS.Aux.buyBar, S.deal
    if deal.deal_gone then
        finish(PHASE.LOST, 'Sold before you could buy it')
        return
    end
    if not stillListed(deal) then
        -- A newer record for this item replaced it; choose afresh.
        letGo()
        finishIdle()
        return
    end
    if GetTime() - S.phaseAt > PREPARE_TIMEOUT then
        NS.Log('stuck ' .. tostring(deal.name) .. ': ' .. describeStuck(deal))
        skip(deal, TIMEOUT_SKIP_SECONDS)
        finish(PHASE.LOST, 'Could not get a live price')
        return
    end
    local selection = env.listing:GetSelection()
    if not selection or selection.record ~= deal then
        S.selectTries = S.selectTries + 1
        if S.selectTries > MAX_SELECT_TRIES then
            NS.Log('unselectable ' .. tostring(deal.name) .. ': ' .. describeStuck(deal))
            skip(deal)
            finish(PHASE.LOST, 'No longer listed')
            return
        end
        selectDeal(deal)
        return
    end
    if env.refreshing or bar.busy() or not barShows(deal) then return end
    local n, cost, profit = sizeNow(deal)
    if n <= 0 then
        finish(PHASE.LOST, 'Not enough free cash')
        return
    end
    S.n, S.cost, S.profit = n, cost, profit
    local label = bar.primary_label() or ''
    if deal.commodity then
        local input = bar.other_input
        if input:GetNumber() ~= n or not S.qtySetAt then
            input:SetText(tostring(n))
            S.qtySetAt = GetTime()
            return
        end
        if GetTime() - S.qtySetAt < LABEL_SETTLE then return end
        if tonumber(string.match(label, '^Buy (%d+) for')) ~= n then return end
    elseif not string.find(label, '^Buy for') then
        return
    end
    S.readyAt = GetTime()
    setPhase(PHASE.READY)
end

local function tickQuoting()
    local bar = NS.Aux.buyBar
    if not bar.busy() then
        retry('The price moved')
        return
    end
    local label = bar.primary_label() or ''
    if string.find(label, '^Confirm ') then
        setPhase(PHASE.CONFIRMING)
    elseif GetTime() - S.phaseAt > NO_ANSWER_SECONDS then
        finish(PHASE.LOST, 'No answer from the auction house')
    end
end

local function confirmNow()
    S.confirmedAt = GetTime()
    setPhase(PHASE.BUYING)
    NS.Aux.buyBar.primary_click()
end

local function tickConfirming()
    local bar = NS.Aux.buyBar
    if not bar.busy() then
        retry('The price moved')
        return
    end
    if S.quote and S.quote > (S.cost or 0) then
        bar.other_input.escape(bar.other_input)
        retry('The price moved')
        return
    end
    if NS.Settings().autoConfirm then confirmNow() end
end

local function tickBuying()
    local deal = S.deal
    if deal.commodity then
        if S.failedEvent then
            retry('The purchase failed')
            return
        end
        -- aux gave up waiting. It may still go through on a slow server, so buying
        -- again here could pay twice.
        if not NS.Aux.buyBar.busy() and not S.boughtEvent and GetTime() - S.phaseAt > 1 then
            finish(PHASE.LOST, 'No answer yet: check your mail before buying again')
            return
        end
    else
        if deal.deal_bought then
            S.boughtEvent = true
        elseif deal.deal_gone then
            finish(PHASE.LOST, 'Sold before you could buy it')
            return
        end
    end
    if S.boughtEvent then
        S.boughtEvent = nil
        if deal.commodity and deal.deal_reason ~= 'vendor' then Snipe.AddHeld(deal.item_id, S.n or 0) end
        NS.Log(string.format('bought %s x %d for %s', deal.name or '?', S.n or 0, coin(S.cost or 0)))
        finish(PHASE.DONE, 'Bought ' .. (S.n or 0) .. ' x ' .. (deal.name or '?') .. ' for ' .. coin(S.cost or 0))
        NS.Aux.RecheckItem(deal.name)
    elseif GetTime() - S.phaseAt > NO_ANSWER_SECONDS then
        -- It may still go through on a slow server, so never buy again automatically.
        finish(PHASE.LOST, 'No answer yet: check your mail before buying again')
    end
end

-- Why the Sniper offers this, in the player's words: 'below vendor price (vendor pays 15c)',
-- '36% of usual price (usual 1s 20c)', or for the plan 'resale bet: list at 12c (63% of fair)'.
function Snipe.Reason(deal, planEntry)
    local reason, pct = deal.deal_reason, tonumber(deal.deal_percent)
    if reason == 'vendor' then
        return 'below vendor price (vendor pays ' .. coin(deal.deal_vendor) .. ')'
    elseif reason == 'usual' then
        return string.format('%d%% of usual price (usual %s)', pct or 0, coin(deal.deal_usual))
    elseif reason == 'plan' then
        local text = 'resale bet'
        if planEntry and (planEntry.t or 0) > 0 then text = text .. ': list at ' .. coin(planEntry.t) end
        if pct then text = text .. string.format(' (%d%% of fair)', pct) end
        return text
    end
    return tostring(reason or '')
end

local function render()
    local phase, deal = S.phase, S.deal
    if phase == PHASE.IDLE then
        hideButton()
        return
    end
    if phase ~= PHASE.READY then reasonText:SetText('') end
    if phase == PHASE.PREPARE then
        showButton(0.25, 0.25, 0.28)
        titleText:SetText(GetTime() - (S.phaseAt or 0) > SLOW_SECONDS and 'Waiting for the auction house...' or 'Checking...')
        detailText:SetText(deal and deal.name or '')
    elseif phase == PHASE.READY then
        -- A below-vendor deal is a sure profit; anything else is a bet on reselling, and must not look the same.
        if deal.deal_reason == 'vendor' then
            showButton(0.05, 0.55, 0.12)
            titleText:SetText('CONFIRM')
        else
            showButton(0.1, 0.3, 0.6)
            titleText:SetText('CONFIRM BET')
        end
        reasonText:SetText(Snipe.Reason(deal, NS.Plan.For(deal.item_id)))
        detailText:SetText(string.format('%d x %s  %s  (+%s)', S.n or 0, deal.name or '?', coin(S.cost), coin(S.profit)))
    elseif phase == PHASE.QUOTING then
        showButton(0.45, 0.4, 0.05)
        titleText:SetText('Getting price...')
    elseif phase == PHASE.CONFIRMING then
        showButton(0.05, 0.55, 0.12)
        titleText:SetText('CONFIRM PRICE')
        detailText:SetText(string.format('%d x %s  %s', S.n or 0, deal.name or '?', coin(S.quote or S.cost)))
    elseif phase == PHASE.BUYING then
        showButton(0.45, 0.4, 0.05)
        titleText:SetText('Buying...')
    elseif phase == PHASE.DONE then
        showButton(0.1, 0.4, 0.55)
        titleText:SetText('BOUGHT')
        detailText:SetText(S.message or '')
    elseif phase == PHASE.LOST then
        showButton(0.55, 0.12, 0.1)
        titleText:SetText('LOST')
        detailText:SetText(S.message or '')
    end
end

function Snipe.Tick()
    if not button then return end
    local aux = NS.Aux
    if not NS.AuctionHouseOpen or not aux.env or not aux.IsTabShown() then
        if S.phase ~= PHASE.IDLE then finishIdle() end
        hideButton()
        return
    end
    local phase = S.phase
    if phase == PHASE.IDLE then
        if GetTime() >= nextChoose and not aux.buyBar.busy() and not aux.env.refreshing then
            nextChoose = GetTime() + CHOOSE_EVERY
            local deal = chooseDeal()
            if deal then begin(deal) end
        end
    elseif phase == PHASE.PREPARE then
        tickPrepare()
    elseif phase == PHASE.READY then
        local deal = S.deal
        if deal.deal_gone then
            finish(PHASE.LOST, 'Sold before you could buy it')
        elseif GetTime() - S.readyAt > READY_TTL then
            skip(deal)
            letGo()
            finishIdle()
        end
    elseif phase == PHASE.QUOTING then
        tickQuoting()
    elseif phase == PHASE.CONFIRMING then
        tickConfirming()
    elseif phase == PHASE.BUYING then
        tickBuying()
    elseif phase == PHASE.DONE or phase == PHASE.LOST then
        if GetTime() - S.phaseAt > (phase == PHASE.DONE and DONE_SECONDS or LOST_SECONDS) then
            -- Release the selection, or the Sniper's rounds stay on hold.
            if phase == PHASE.LOST or not aux.scan.is_scanning() then letGo() end
            finishIdle()
        end
    end
    render()
end

-- The one entry point for a real press. Pressing again while a purchase is under
-- way does nothing, so a frantic double-click cannot double-buy. source: 'click',
-- 'wheel' or 'key' (a key binding, or the Windows button's relayed key).
function Snipe.Press(source)
    local phase, deal = S.phase, S.deal
    -- Only presses that can do something are logged: rapid clicking flooded the link.
    if phase == PHASE.IDLE or phase == PHASE.READY or phase == PHASE.CONFIRMING then
        NS.Log('press (' .. tostring(source or '?') .. ') in ' .. tostring(PHASE_NAMES[phase]))
    end
    if phase == PHASE.READY then
        if GetTime() < (S.armedAt or 0) then return end
        -- The wallet can change between the offer and the press.
        local n = sizeNow(deal)
        if n ~= S.n then
            S.qtySetAt = nil
            setPhase(PHASE.PREPARE)
            return
        end
        S.quote, S.boughtEvent, S.failedEvent = nil, nil, nil
        if deal.commodity then
            setPhase(PHASE.QUOTING)
        else
            setPhase(PHASE.BUYING)
        end
        NS.Aux.buyBar.primary_click()
    elseif phase == PHASE.CONFIRMING and not NS.Settings().autoConfirm then
        confirmNow()
    end
end

function ForeverTrader_Confirm()
    Snipe.Press('key')
end

-- Right-click: not this one, thanks.
function Snipe.Skip()
    local deal = S.deal
    if not deal then return end
    if S.phase == PHASE.PREPARE or S.phase == PHASE.READY then
        skip(deal)
        letGo()
        finishIdle()
    end
end

function Snipe.OnEvent(event, ...)
    if event == 'COMMODITY_PRICE_UPDATED' then
        local _, total = ...
        S.quote = total
    elseif event == 'COMMODITY_PURCHASE_SUCCEEDED' then
        if S.phase == PHASE.BUYING then S.boughtEvent = true end
    elseif event == 'COMMODITY_PURCHASE_FAILED' or event == 'AUCTION_HOUSE_SHOW_ERROR' then
        if S.phase == PHASE.BUYING then S.failedEvent = true end
    elseif event == 'ADDON_ACTION_BLOCKED' or event == 'ADDON_ACTION_FORBIDDEN' then
        local addon, func = ...
        if tostring(func):find('ConfirmCommoditiesPurchase') then
            -- The game wants a real press for this step too, so ask for one from now on.
            NS.Settings().autoConfirm = false
            NS.Log('auto-confirm blocked by the game; using a second press instead')
        end
        NS.Log('blocked: ' .. tostring(addon) .. ' ' .. tostring(func))
    end
end

function Snipe.Create()
    if button then return end
    button = CreateFrame('Button', 'ForeverTraderConfirmButton', UIParent, 'BackdropTemplate')
    button:SetSize(340, 68)
    button:SetFrameStrata('FULLSCREEN_DIALOG')
    button:SetFrameLevel(500)
    button:SetClampedToScreen(true)
    button:SetBackdrop({bgFile = 'Interface\\Buttons\\WHITE8X8', edgeFile = 'Interface\\Buttons\\WHITE8X8', edgeSize = 2})
    button:SetBackdropBorderColor(1, 1, 1, 0.85)
    button:RegisterForClicks('LeftButtonUp', 'RightButtonUp')
    titleText = button:CreateFontString(nil, 'OVERLAY', 'GameFontNormalLarge')
    titleText:SetPoint('TOP', 0, -7)
    titleText:SetTextColor(1, 1, 1)
    reasonText = button:CreateFontString(nil, 'OVERLAY', 'GameFontNormal')
    reasonText:SetPoint('CENTER', 0, -1)
    reasonText:SetWidth(330)
    reasonText:SetWordWrap(false)
    reasonText:SetTextColor(1, 0.85, 0.3)
    detailText = button:CreateFontString(nil, 'OVERLAY', 'GameFontHighlight')
    detailText:SetPoint('BOTTOM', 0, 8)
    detailText:SetWidth(330)
    detailText:SetWordWrap(false)
    button:SetScript('OnClick', function(self, mouseButton)
        if mouseButton == 'RightButton' then
            Snipe.Skip()
        else
            -- The relayed key clicks this button through an override binding, wherever the cursor is.
            Snipe.Press(self:IsMouseOver() and 'click' or 'key')
        end
    end)
    button:EnableMouseWheel(true)
    button:SetScript('OnMouseWheel', function() Snipe.Press('wheel') end)
    button:Hide()
end
