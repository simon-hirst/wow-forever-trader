-- FOREVER_TRADER_QUICK_SNIPE_BEGIN v0.3.2
-- Optional UI-only extension for auxForever/tabs/sniper/frame.lua.
-- Executes inside aux.tabs.sniper's existing module environment, never unattended.
do
    local quickEnabled = true
    local currentQuickDeal, previousAnchorDeal
    local nextQuickRefresh = 0
    local quickButton = CreateFrame('Button', 'ForeverTraderQuickSniperButton', UIParent, 'UIPanelButtonTemplate')
    quickButton:SetSize(232, 48)
    quickButton:SetFrameStrata('DIALOG')
    quickButton:SetClampedToScreen(true)
    quickButton:Hide()
    local quickHelp = quickButton:CreateFontString(nil, 'OVERLAY', 'GameFontNormalSmall')
    quickHelp:SetPoint('TOP', quickButton, 'BOTTOM', 0, -3)
    quickHelp:SetText('Click to select / buy / confirm')

    local function quickEligible(deal)
        if not deal or deal.deal_gone or deal.deal_bought or deal.deal_reason ~= 'vendor' then return false end
        if not deal.deal_found or time() - deal.deal_found > 40 then return false end
        local unit = deal.unit_buyout_price or 0
        if unit <= 0 or (deal.deal_profit or 0) <= 0 then return false end
        return unit <= GetMoney() - 500 -- keep at least 5 silver available
    end

    local function chooseQuickDeal()
        local best,score
        for _, deal in ipairs(deals or {}) do
            if quickEligible(deal) then
                -- Favour absolute guaranteed NPC profit; quantities are estimates until refreshed.
                local n = deal.commodity and math.min(deal.count or 1,5) or 1
                local value = (deal.deal_profit or 0) * n
                if not best or value > score then best,score=deal,value end
            end
        end
        return best
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

    quickButton:SetScript('OnClick',function(self)
        local deal=currentQuickDeal
        if not quickEligible(deal) or not frame:IsShown() or not running then return end
        if checked ~= deal then
            listing:SetSelectedRecord(deal)
            update_selection() -- auxForever refreshes the chosen auction before offering Buy
            self:Disable()      -- don't turn a double-click into a stale-price purchase
            return
        end
        if refreshing or aux.bid_in_progress() or deal.deal_gone then return end
        -- Invoke auxForever's EXISTING buy/quote/confirm workflow, once per real click.
        -- Commodities still require a separate user click on the server quote.
        buy_bar.primary_click()
        nextQuickRefresh = 0
    end)

    local quickHost = CreateFrame('Frame', nil, UIParent)
    quickHost:SetScript('OnUpdate',function(_,elapsed)
        if GetTime() < nextQuickRefresh then return end
        nextQuickRefresh = GetTime() + 0.15
        if not quickEnabled or not frame:IsShown() or not running then
            quickButton:Hide()
            currentQuickDeal=nil
            return
        end
        local deal = chooseQuickDeal()
        if not deal then
            quickButton:Hide()
            currentQuickDeal=nil
            return
        end
        currentQuickDeal=deal
        if deal ~= previousAnchorDeal then
            previousAnchorDeal=deal
            anchorAtCursor() -- button stays put after appearing; never chases a moving cursor
        end
        quickButton:Show()
        if checked ~= deal then
            quickButton:SetText('CHECK  '..(deal.name or ('Item '..tostring(deal.item_id))))
            quickButton:Enable()
        elseif refreshing or aux.bid_in_progress() then
            quickButton:SetText('Checking live price...')
            quickButton:Disable()
        else
            local action = buy_bar.primary_label() or 'Buy'
            quickButton:SetText(action)
            quickButton:Enable()
        end
    end)
    SLASH_FOREVERTRADERQUICK1='/ftquick'
    SlashCmdList.FOREVERTRADERQUICK=function()
        quickEnabled = not quickEnabled
        if not quickEnabled then quickButton:Hide() end
        print('Forever Trader quick vendor sniper: '..(quickEnabled and 'ON' or 'OFF'))
    end
end
-- FOREVER_TRADER_QUICK_SNIPE_END
