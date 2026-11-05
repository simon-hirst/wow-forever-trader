-- Forever Trader Bridge v0.2.0: passive, read-only collection of the player's
-- normal Auction House detail search results, including searches via auxForever.
-- No automation, purchases, uploads, or interaction with seller identities.
local AH = C_AuctionHouse
local MAX_BOOKS, MAX_LEVELS = 300, 250
local frame = CreateFrame('Frame')

local function ready()
    return AH and type(AH.GetNumCommoditySearchResults) == 'function'
end
local function book_id(itemID, itemKey, kind)
    if kind == 'commodity' then return tostring(itemID) end
    local k = itemKey or {}
    return tostring(itemID)..':'..tostring(k.itemLevel or 0)..':'..tostring(k.itemSuffix or 0)..':'..tostring(k.battlePetSpeciesID or 0)
end
local function item_name(itemID, itemKey)
    local name
    if itemKey and AH.GetItemKeyInfo then
        local ok, info = pcall(AH.GetItemKeyInfo, itemKey)
        if ok and info then name = info.itemName end
    end
    if not name and GetItemInfo then name = GetItemInfo(itemID) end
    return name or ('Item #'..tostring(itemID))
end
local function vendor_price(itemID)
    if not GetItemInfo then return 0 end
    local ok, result = pcall(function() return select(11, GetItemInfo(itemID)) end)
    return (ok and type(result)=='number' and result>0) and result or 0
end
local function save_book(book)
    if type(ForeverTraderBridgeDB)~='table' then return end
    local books = ForeverTraderBridgeDB.books
    if not books then books={}; ForeverTraderBridgeDB.books=books end
    -- Suppress duplicate writes when this item's depth hasn't changed.
    books[book.key] = book
    local count = 0
    for _ in pairs(books) do count=count+1 end
    if count>MAX_BOOKS then
        local oldestKey, oldest
        for key, entry in pairs(books) do
            if not oldest or (entry.ts or 0)<oldest then oldestKey,oldest=key,entry.ts or 0 end
        end
        if oldestKey then books[oldestKey]=nil end
    end
end
local function store_commodity(itemID)
    if type(itemID)~='number' or not ready() then return end
    local ok,count=pcall(AH.GetNumCommoditySearchResults,itemID)
    if not ok or type(count)~='number' or count<=0 then return end
    local levels,prices={},{}
    for i=1,math.min(count,MAX_LEVELS) do
        local good, rec=pcall(AH.GetCommoditySearchResultInfo,itemID,i)
        if good and rec and type(rec.unitPrice)=='number' and rec.unitPrice>0 and rec.quantity and rec.quantity>0 then
            local p=math.ceil(rec.unitPrice)
            prices[p]=(prices[p] or 0)+rec.quantity
        end
    end
    for p,q in pairs(prices) do levels[#levels+1]={p=p,q=q} end
    table.sort(levels,function(a,b) return a.p<b.p end)
    if #levels==0 then return end
    local full=false
    if AH.HasFullCommoditySearchResults then
        local valid,value=pcall(AH.HasFullCommoditySearchResults,itemID)
        full=valid and value and count<=MAX_LEVELS or false
    end
    local total=0
    if AH.GetCommoditySearchResultsQuantity then
        local valid,value=pcall(AH.GetCommoditySearchResultsQuantity,itemID)
        if valid and type(value)=='number' then total=value end
    end
    save_book({key=book_id(itemID,nil,'commodity'),item_id=itemID,
        kind='commodity',name=item_name(itemID),ts=time(),full=full,
        vendor=vendor_price(itemID),total=total,levels=levels})
end
local function store_item(itemKey)
    if not itemKey or type(itemKey.itemID)~='number' or not ready() then return end
    local ok,count=pcall(AH.GetNumItemSearchResults,itemKey)
    if not ok or type(count)~='number' or count<=0 then return end
    local prices,levels={},{}
    for i=1,math.min(count,MAX_LEVELS) do
        local good,rec=pcall(AH.GetItemSearchResultInfo,itemKey,i)
        if good and rec and type(rec.buyoutAmount)=='number' and rec.buyoutAmount>0 then
            -- The purchase is an entire auction/stack. q counts auctions, NOT item units.
            local p=math.ceil(rec.buyoutAmount)
            prices[p]=(prices[p] or 0)+1
        end
    end
    for p,q in pairs(prices) do levels[#levels+1]={p=p,q=q} end
    table.sort(levels,function(a,b) return a.p<b.p end)
    if #levels==0 then return end
    local full=false
    if AH.HasFullItemSearchResults then
        local valid,value=pcall(AH.HasFullItemSearchResults,itemKey)
        full=valid and value and count<=MAX_LEVELS or false
    end
    save_book({key=book_id(itemKey.itemID,itemKey,'item'),item_id=itemKey.itemID,
        kind='item',name=item_name(itemKey.itemID,itemKey),ts=time(),full=full,
        vendor=vendor_price(itemKey.itemID),total=count,levels=levels})
end
frame:RegisterEvent('ADDON_LOADED')
frame:RegisterEvent('COMMODITY_SEARCH_RESULTS_UPDATED')
frame:RegisterEvent('COMMODITY_SEARCH_RESULTS_ADDED')
frame:RegisterEvent('ITEM_SEARCH_RESULTS_UPDATED')
frame:RegisterEvent('ITEM_SEARCH_RESULTS_ADDED')
frame:SetScript('OnEvent',function(_,event,...)
    if event=='ADDON_LOADED' then
        if ...=='ForeverTraderBridge' then
            if type(ForeverTraderBridgeDB)~='table' or ForeverTraderBridgeDB.version~=1 then
                ForeverTraderBridgeDB={version=1,market='PvP',books={}}
            end
        end
    elseif event=='COMMODITY_SEARCH_RESULTS_UPDATED' or event=='COMMODITY_SEARCH_RESULTS_ADDED' then
        local id=...
        local ok,err=pcall(store_commodity,id)
        if not ok then print('ForeverTraderBridge commodity error: '..tostring(err)) end
    elseif event=='ITEM_SEARCH_RESULTS_UPDATED' or event=='ITEM_SEARCH_RESULTS_ADDED' then
        local key=...
        local ok,err=pcall(store_item,key)
        if not ok then print('ForeverTraderBridge item error: '..tostring(err)) end
    end
end)
SLASH_FOREVERTRADERBRIDGE1='/ftb'
SlashCmdList['FOREVERTRADERBRIDGE']=function()
    local n=0
    for _ in pairs((ForeverTraderBridgeDB and ForeverTraderBridgeDB.books) or {}) do n=n+1 end
    print('Forever Trader Bridge: '..n..' recent item order books recorded. Type /reload to save them for the Windows app.')
end

-- v0.3 transaction journal. Observe official purchase events and the player's
-- own inventory/money changes. Never place orders or invoke merchant actions.
-- Only unambiguous, money-matched receipts are marked confirmed; everything
-- else is REVIEW and deliberately excluded from automatic realised P&L.
local txnFrame = CreateFrame('Frame')
local MAX_EVENTS = 1600
local mode, previousMoney, previousBags, merchantMoney, merchantBags
local pendingAH, moneyDebits = {}, {}
local flushQueued, flushToken = false, 0
local auctionCache = {}
local characterID = 'Unknown'

local function bagsSnapshot()
    local contents = {}
    for bag=0,4 do
        local slots
        if C_Container and C_Container.GetContainerNumSlots then
            slots=C_Container.GetContainerNumSlots(bag)
        elseif GetContainerNumSlots then
            slots=GetContainerNumSlots(bag)
        end
        for slot=1,(slots or 0) do
            local id,qty
            if C_Container and C_Container.GetContainerItemID then
                id=C_Container.GetContainerItemID(bag,slot)
                local info=C_Container.GetContainerItemInfo and C_Container.GetContainerItemInfo(bag,slot)
                qty=info and (info.stackCount or info.quantity)
            elseif GetContainerItemID then
                id=GetContainerItemID(bag,slot)
                if GetContainerItemInfo then
                    local _, count=GetContainerItemInfo(bag,slot)
                    qty=count
                end
            end
            if type(id)=='number' and id>0 then contents[id]=(contents[id] or 0)+(qty or 1) end
        end
    end
    return contents
end
local function ensureTxnDB()
    if type(ForeverTraderBridgeDB)~='table' then return nil end
    local db=ForeverTraderBridgeDB
    if type(db.activity)~='table' then db.activity={} end
    if type(db.wallets)~='table' then db.wallets={} end
    db.activity_seq=db.activity_seq or 0
    return db
end
local function recordWallet()
    local db=ensureTxnDB()
    if db and GetMoney then
        db.wallets[characterID]={character=characterID,copper=GetMoney(),ts=time()}
    end
end
local function eventRecord(kind,id,qty,total,status,note)
    local db=ensureTxnDB()
    if not db then return end
    db.activity_seq=db.activity_seq+1
    local seq=db.activity_seq
    local row={uid=tostring(seq),ts=time(),character=characterID,kind=kind,
        item_id=id or 0,qty=qty or 0,total=total or 0,
        status=status or 'review',note=note or ''}
    table.insert(db.activity,row)
    if #db.activity>MAX_EVENTS then table.remove(db.activity,1) end
end
local function clearOldDebits()
    local now=GetTime()
    for i=#moneyDebits,1,-1 do
        if now-moneyDebits[i].ts>4 then table.remove(moneyDebits,i) end
    end
end
local function settleMerchant()
    local money=GetMoney()
    local bags=bagsSnapshot()
    if merchantMoney and money>merchantMoney then
        local sold, expected, unknown = {}, 0, false
        for id,was in pairs(merchantBags or {}) do
            local q=was-(bags[id] or 0)
            if q>0 then
                local unit=vendor_price(id)
                if unit<=0 then unknown=true end
                sold[#sold+1]={id=id,q=q,unit=unit}
                expected=expected+q*unit
            end
        end
        local gained=money-merchantMoney
        if #sold>0 and not unknown and gained==expected then
            for _,v in ipairs(sold) do
                eventRecord('VENDOR_SELL',v.id,v.q,v.q*v.unit,'confirmed',
                    'Vendor payout matched bag decrease and wallet increase')
            end
        else
            eventRecord('VENDOR_MONEY',0,0,gained,'review',
                'Vendor wallet gain could not be reliably attributed to individual bag items')
        end
    end
    merchantMoney,merchantBags=money,bags
end
local function settleAH()
    if #pendingAH==0 then return end
    clearOldDebits()
    -- The client's two item-purchase notifications can refer to the same buy.
    -- Prefer the auctionID receipt whenever both are observed together.
    local completed={}
    for _,p in ipairs(pendingAH) do
        if p.event=='AUCTION_HOUSE_PURCHASE_COMPLETED' and p.id then completed[p.id]=true end
    end
    local relevant={}
    for _,p in ipairs(pendingAH) do
        if p.event~='ITEM_PURCHASED' or not completed[p.id] then
            relevant[#relevant+1]=p
        end
    end
    if #relevant==1 and #moneyDebits==1 then
        local p=relevant[1]
        local debit=moneyDebits[1].amount
        local valid=(p.qty or 0)>0 and p.id and p.id>0 and debit>0
        -- Item auctions have a cached exact buyout. Require it to match wallet.
        if p.expected and p.expected~=debit then valid=false end
        if valid then
            eventRecord('AH_BUY',p.id,p.qty,debit,'confirmed',
                'Purchase event matched a single wallet debit')
        else
            eventRecord('AH_BUY',p.id,p.qty,debit,'review',
                'Amount or quantity ambiguous; check auction receipts')
        end
        moneyDebits={}
    else
        for _,p in ipairs(relevant) do
            eventRecord('AH_BUY',p.id,p.qty,p.expected or 0,'review',
                'No unique matching wallet debit; review manually')
        end
    end
    pendingAH={}
end
local function flush()
    flushQueued=false
    if mode=='MERCHANT' then settleMerchant()
    elseif mode=='AH' then settleAH() end
    previousMoney=GetMoney()
    previousBags=bagsSnapshot()
    recordWallet()
end
local function scheduleFlush()
    flushToken=flushToken+1
    local token=flushToken
    if C_Timer and C_Timer.After then
        C_Timer.After(.65,function()
            if token==flushToken then flush() end
        end)
    else
        flush()
    end
end
local function pushPurchase(id,qty,event,expected)
    pendingAH[#pendingAH+1]={id=id,qty=qty,event=event,expected=expected}
    scheduleFlush()
end
-- We cache only auction IDs and prices encountered during the player's own
-- detailed item searches. This cache is not exported to disk.
local function cacheItemAuctions(itemKey)
    if not itemKey or not AH or not AH.GetNumItemSearchResults then return end
    local ok,count=pcall(AH.GetNumItemSearchResults,itemKey)
    if not ok or type(count)~='number' then return end
    for i=1,math.min(count,400) do
        local good,r=pcall(AH.GetItemSearchResultInfo,itemKey,i)
        if good and r and r.auctionID and r.buyoutAmount and r.buyoutAmount>0 then
            auctionCache[r.auctionID]={id=itemKey.itemID,qty=r.quantity or 1,
                cost=r.buyoutAmount,ts=GetTime()}
        end
    end
end
for _,ev in ipairs({
    'PLAYER_LOGIN','PLAYER_ENTERING_WORLD','PLAYER_MONEY','BAG_UPDATE_DELAYED',
    'AUCTION_HOUSE_SHOW','AUCTION_HOUSE_CLOSED','MERCHANT_SHOW','MERCHANT_CLOSED',
    'COMMODITY_PURCHASED','ITEM_PURCHASED','AUCTION_HOUSE_PURCHASE_COMPLETED',
    'ITEM_SEARCH_RESULTS_UPDATED','ITEM_SEARCH_RESULTS_ADDED'
}) do txnFrame:RegisterEvent(ev) end

txnFrame:SetScript('OnEvent',function(_,event,...)
    if event=='PLAYER_LOGIN' or event=='PLAYER_ENTERING_WORLD' then
        local name=UnitName('player') or 'Unknown'
        local server=GetRealmName and GetRealmName() or 'PvP'
        characterID=name..'-'..server
        previousMoney=GetMoney();previousBags=bagsSnapshot();recordWallet()
    elseif event=='AUCTION_HOUSE_SHOW' then
        if mode=='MERCHANT' then settleMerchant() end
        mode='AH';pendingAH={};moneyDebits={}
        previousMoney=GetMoney();previousBags=bagsSnapshot();recordWallet()
    elseif event=='AUCTION_HOUSE_CLOSED' then
        -- Preserve a pending purchase for settlement even if the window closes.
        scheduleFlush()
        if C_Timer and C_Timer.After then C_Timer.After(1,function() if mode=='AH' then mode=nil end end) end
    elseif event=='MERCHANT_SHOW' then
        mode='MERCHANT';merchantMoney=GetMoney();merchantBags=bagsSnapshot()
        previousMoney=merchantMoney;previousBags=merchantBags;recordWallet()
    elseif event=='MERCHANT_CLOSED' then
        flushToken=flushToken+1;settleMerchant();mode=nil
        previousMoney=GetMoney();recordWallet()
    elseif event=='PLAYER_MONEY' then
        local nowMoney=GetMoney()
        if mode=='AH' and previousMoney and nowMoney<previousMoney then
            moneyDebits[#moneyDebits+1]={amount=previousMoney-nowMoney,ts=GetTime()}
        end
        previousMoney=nowMoney
        recordWallet()
        if mode=='AH' or mode=='MERCHANT' then scheduleFlush() end
    elseif event=='BAG_UPDATE_DELAYED' then
        if mode=='MERCHANT' then scheduleFlush() end
    elseif event=='COMMODITY_PURCHASED' then
        local id,qty=...
        if type(id)=='number' and type(qty)=='number' then
            pushPurchase(id,qty,event)
        end
    elseif event=='ITEM_PURCHASED' then
        local id=...
        if type(id)=='number' then pushPurchase(id,0,event) end -- qty unknown without auctionID
    elseif event=='AUCTION_HOUSE_PURCHASE_COMPLETED' then
        local auctionID=...
        local info=auctionCache[auctionID]
        if info then
            pushPurchase(info.id,info.qty,event,info.cost)
            auctionCache[auctionID]=nil
        else
            pushPurchase(0,0,event)
        end
    elseif event=='ITEM_SEARCH_RESULTS_UPDATED' or event=='ITEM_SEARCH_RESULTS_ADDED' then
        local itemKey=...
        pcall(cacheItemAuctions,itemKey)
    end
end)
SLASH_FOREVERTRADERACTIVITY1='/ftmoney'
SlashCmdList.FOREVERTRADERACTIVITY=function()
    local db=ensureTxnDB()
    print('Forever Trader: wallet '..(GetCoinTextureString and GetCoinTextureString(GetMoney()) or tostring(GetMoney())..' copper'))
    if db then print('Forever Trader: '..#db.activity..' captured transaction events ('..tostring(db.activity_seq)..' total). Type /reload to sync the Windows app.') end
end
