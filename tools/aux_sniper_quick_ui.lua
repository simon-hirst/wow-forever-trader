-- FOREVER_TRADER_QUICK_SNIPE_BEGIN v0.3.3
-- Optional user-click-only overlay for auxForever/tabs/sniper/frame.lua.
-- Everything still goes through auxForever's own live refresh and Buy / Confirm logic.
do
    local quickEnabled = true
    local currentQuickDeal, previousAnchorDeal, preparedDeal
    local nextQuickRefresh = 0
    local RESERVE_COPPER, MAX_UNITS = 500, 5
    local quickButton = CreateFrame('Button', 'ForeverTraderQuickSniperButton', UIParent, 'UIPanelButtonTemplate')
    quickButton:SetSize(240, 48)
    quickButton:SetFrameStrata('DIALOG')
    quickButton:SetClampedToScreen(true)
    quickButton:Hide()
    local quickHelp = quickButton:CreateFontString(nil, 'OVERLAY', 'GameFontNormalSmall')
    quickHelp:SetPoint('TOP', quickButton, 'BOTTOM', 0, -3)
    quickHelp:SetText('Manual clicks only - NPC vendor deals')

    -- The Sniper's "checked" is private to core.lua; use the real UI selection.
    local function selectedDeal()
        local selection = listing:GetSelection()
        return selection and selection.record or nil
    end

    local function quickEligible(deal, allowOld)
        if not deal or deal.deal_gone or deal.deal_bought or deal.deal_reason ~= 'vendor' then return false end
        if not allowOld and (not deal.deal_found or time() - deal.deal_found > 40) then return false end
        local unit = math.ceil(deal.unit_buyout_price or 0)
        local vendor = deal.deal_vendor or 0
        local cost = deal.commodity and unit or (deal.buyout_price or unit)
        return unit > 0 and vendor > unit and (deal.deal_profit or 0) > 0
            and cost > 0 and cost <= GetMoney() - RESERVE_COPPER
    end

    local function chooseQuickDeal()
        -- Never switch to another item while a server quote is active.
        if buy_bar.busy() then
            local selected = selectedDeal()
            return quickEligible(selected, true) and selected or nil
        end
        local best, score
        for _, deal in ipairs(deals or {}) do
            if quickEligible(deal, false) then
                local n = deal.commodity and math.min(deal.count or 1, MAX_UNITS) or 1
                local value = (deal.deal_profit or 0) * n
                if not best or value > score then best, score = deal, value end
            end
        end
        return best
    end

    -- Make a SMALL buy from freshly checked sub-vendor price tiers.
    local function affordableCommodityQuantity(deal)
        local remainingCash = GetMoney() - RESERVE_COPPER
        local vendor = deal.deal_vendor or 0
        local item = info.item(deal.item_id)
        local maxStack = item and item.max_stack or 1
        local maxQty = math.min(MAX_UNITS, maxStack)
        local selectedQty = 0
        for _, tier in ipairs(deal.deal_tiers or {}) do
            local price = math.ceil(tier.unit_buyout_price or tier.commodity_unit_price or 0)
            local available = tier.count or 0
            if price > 0 and price < vendor and available > 0 then
                local n = math.min(available, maxQty - selectedQty, math.floor(remainingCash / price))
                selectedQty = selectedQty + n
                remainingCash = remainingCash - n * price
            end
            if selectedQty >= maxQty then break end
        end
        return selectedQty
    end

    local function quotePermitted(label)
        if not label or not string.find(label, 'Confirm ', 1, true) then return false end
        local quoted = money.from_string(string.sub(label, 9))
        return quoted and quoted > 0 and quoted <= GetMoney() - RESERVE_COPPER
    end

    local function anchorAtCursor()
        local x,y=GetCursorPosition()
        local scale=UIParent:GetEffectiveScale()
        quickButton:ClearAllPoints()
        if x and y and scale and scale>0 then
            quickButton:SetPoint('BOTTOMLEFT', UIParent, 'BOTTOMLEFT', x/scale+42, y/scale+28)
        else
            quickButton:SetPoint('CENTER', UIParent, 'CENTER', 0, 180)
        end
    end

    quickButton:SetScript('OnClick', function(self)
        local deal = currentQuickDeal
        if not quickEnabled or not frame:IsShown() or not running or not quickEligible(deal, true) then return end
        if selectedDeal() ~= deal then
            if buy_bar.busy() then return end
            preparedDeal = nil
            listing:SetSelectedRecord(deal)
            update_selection() -- auxForever does its own live recheck
            self:Disable() -- double-click cannot purchase stale listing
            return
        end
        if refreshing or aux.bid_in_progress() then return end
        local label = buy_bar.primary_label() or ''
        if buy_bar.busy() and not quotePermitted(label) then return end
        if deal.commodity and not buy_bar.busy() and preparedDeal ~= deal then
            local n = affordableCommodityQuantity(deal)
            if n < 1 or not buy_bar.other_input then self:Disable(); return end
            buy_bar.other_input:SetText(tostring(n))
            preparedDeal = deal
        end
        -- Exactly one user click -> one original buy-bar action.
        buy_bar.primary_click()
        nextQuickRefresh = 0
    end)

    local quickHost = CreateFrame('Frame', nil, UIParent)
    quickHost:SetScript('OnUpdate', function()
        if GetTime() < nextQuickRefresh then return end
        nextQuickRefresh = GetTime() + 0.15
        if not quickEnabled or not frame:IsShown() or not running then
            quickButton:Hide()
            currentQuickDeal, preparedDeal, previousAnchorDeal = nil, nil, nil
            return
        end
        local deal = chooseQuickDeal()
        if not deal then
            quickButton:Hide()
            currentQuickDeal, preparedDeal, previousAnchorDeal = nil, nil, nil
            return
        end
        if deal ~= currentQuickDeal then preparedDeal = nil end
        currentQuickDeal = deal
        if deal ~= previousAnchorDeal then
            previousAnchorDeal = deal
            anchorAtCursor() -- never chase a moving cursor
        end
        quickButton:Show()
        if selectedDeal() ~= deal then
            quickButton:SetText('CHECK  ' .. (deal.name or ('Item ' .. tostring(deal.item_id))))
            quickButton:Enable()
        elseif refreshing or aux.bid_in_progress() then
            quickButton:SetText('Checking live price...')
            quickButton:Disable()
        else
            local label = buy_bar.primary_label() or 'Buy'
            if buy_bar.busy() and not quotePermitted(label) then
                quickButton:SetText(string.find(label, 'Confirm ', 1, true) and 'Quote exceeds reserve' or 'Waiting for quote...')
                quickButton:Disable()
            else
                quickButton:SetText(label)
                quickButton:Enable()
            end
        end
    end)
    SLASH_FOREVERTRADERQUICK1='/ftquick'
    SlashCmdList.FOREVERTRADERQUICK=function()
        quickEnabled = not quickEnabled
        if not quickEnabled then quickButton:Hide() end
        previousAnchorDeal = nil
        print('Forever Trader quick vendor sniper: '..(quickEnabled and 'ON' or 'OFF'))
    end
end
-- FOREVER_TRADER_QUICK_SNIPE_END
