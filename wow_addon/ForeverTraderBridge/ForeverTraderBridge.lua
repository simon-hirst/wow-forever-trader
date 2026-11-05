-- Forever Trader Bridge: order-book and wallet receipts. Market.lua records the
-- wider market, Snipe.lua owns the cursor Confirm button (one real press per
-- purchase), and Link.lua carries data to the desktop app.
local _, NS = ...
local AH = C_AuctionHouse
local MAX_BOOKS, MAX_LEVELS = 300, 250
local frame = CreateFrame('Frame')
-- Forever moved these into C_Item; the old global is nil, which made every vendor
-- price 0 and every name 'Item #id' in earlier versions of this addon.
local GetItemInfo = (C_Item and C_Item.GetItemInfo) or _G.GetItemInfo
NS.GetItemInfo = GetItemInfo

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
-- An event this client does not have must not stop the others registering.
local function listen(target, event)
    local ok = pcall(target.RegisterEvent, target, event)
    if not ok then NS.unknownEvents = (NS.unknownEvents or '') .. ' ' .. event end
end
NS.Listen = listen
listen(frame, 'ADDON_LOADED')
listen(frame, 'COMMODITY_SEARCH_RESULTS_UPDATED')
listen(frame, 'COMMODITY_SEARCH_RESULTS_ADDED')
listen(frame, 'ITEM_SEARCH_RESULTS_UPDATED')
listen(frame, 'ITEM_SEARCH_RESULTS_ADDED')
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

-- Transaction evidence. Keep uncertain receipts for manual review. Only reconcile sales after both the
-- merchant wallet change and corresponding inventory change can be observed.
local txnFrame = CreateFrame('Frame')
local MAX_EVENTS, MAX_WALLET_CHANGES = 1600, 700
local mode, previousMoney, merchantMoney, merchantBags, merchantUnitPrices
local pendingAH, moneyDebits = {}, {}
local flushToken, merchantToken = 0, 0
local auctionCache = {}
local commodityIntent, lastQuote, lastCommodity
local characterID = 'Unknown'

local function bagSnapshot()
    local contents, prices = {}, {}
    for bag=0,4 do
        local slots = 0
        if C_Container and C_Container.GetContainerNumSlots then
            slots = C_Container.GetContainerNumSlots(bag) or 0
        elseif GetContainerNumSlots then
            slots = GetContainerNumSlots(bag) or 0
        end
        for slot=1,slots do
            local itemID, qty, link
            if C_Container and C_Container.GetContainerItemInfo then
                local info = C_Container.GetContainerItemInfo(bag,slot)
                if info then
                    itemID = info.itemID
                    qty = info.stackCount or info.quantity
                    link = info.hyperlink
                end
            end
            if not itemID and C_Container and C_Container.GetContainerItemID then
                itemID = C_Container.GetContainerItemID(bag,slot)
            end
            if not itemID and GetContainerItemID then itemID = GetContainerItemID(bag,slot) end
            if not qty and GetContainerItemInfo then
                local _,count = GetContainerItemInfo(bag,slot)
                qty = count
            end
            if not link then
                if C_Container and C_Container.GetContainerItemLink then
                    link = C_Container.GetContainerItemLink(bag,slot)
                elseif GetContainerItemLink then
                    link = GetContainerItemLink(bag,slot)
                end
            end
            if type(itemID)=='number' and itemID>0 then
                local quantity = (type(qty)=='number' and qty>0) and qty or 1
                contents[itemID] = (contents[itemID] or 0) + quantity
                -- Cache the actual item-link vendor payout while the item is still in a bag.
                -- Once sold, GetItemInfo(itemID) can return nil when item data is uncached.
                local unit = 0
                if link and GetItemInfo then
                    local ok, price = pcall(function() return select(11,GetItemInfo(link)) end)
                    if ok and type(price)=='number' then unit=price end
                end
                if unit<=0 then unit=vendor_price(itemID) end
                if prices[itemID] and prices[itemID]~=unit then
                    prices[itemID]=-1 -- inconsistent variants: never auto-confirm
                elseif not prices[itemID] then
                    prices[itemID]=unit
                end
            end
        end
    end
    return contents, prices
end

local function ensureTxnDB()
    if type(ForeverTraderBridgeDB)~='table' then return nil end
    local db=ForeverTraderBridgeDB
    if type(db.activity)~='table' then db.activity={} end
    if type(db.wallets)~='table' then db.wallets={} end
    if type(db.wallet_changes)~='table' then db.wallet_changes={} end
    db.activity_seq=db.activity_seq or 0
    db.wallet_seq=db.wallet_seq or 0
    return db
end

local function recordWallet()
    local db=ensureTxnDB()
    if not db or not GetMoney then return end
    local money=GetMoney()
    local previous=db.wallets[characterID]
    if not previous or previous.copper~=money then
        db.wallet_seq=db.wallet_seq+1
        db.wallet_changes[#db.wallet_changes+1]={
            uid=tostring(db.wallet_seq),character=characterID,copper=money,
            delta=previous and money-previous.copper or 0,
            context=mode or 'WORLD',ts=time()}
        if #db.wallet_changes>MAX_WALLET_CHANGES then table.remove(db.wallet_changes,1) end
    end
    db.wallets[characterID]={character=characterID,copper=money,ts=time()}
end

local function eventRecord(kind,id,qty,total,status,note)
    local db=ensureTxnDB()
    if not db then return end
    db.activity_seq=db.activity_seq+1
    local record={uid=tostring(db.activity_seq),ts=time(),
        character=characterID,kind=kind,item_id=id or 0,qty=qty or 0,
        total=total or 0,status=status or 'review',note=note or ''}
    db.activity[#db.activity+1]=record
    if #db.activity>MAX_EVENTS then table.remove(db.activity,1) end
    -- A confirmed receipt is backed by the actual money and bag/purchase
    -- evidence above. Send it over the link immediately, using the same UID
    -- as SavedVariables so /reload can never count it a second time.
    if record.status=='confirmed' and NS.Log then
        local fields={record.uid,record.ts,record.character,record.kind,
            record.item_id,record.qty,record.total,record.status}
        for i,value in ipairs(fields) do fields[i]=tostring(value) end
        NS.Log('FTREC1|'..table.concat(fields,'|'))
    end
end

local function settleMerchant(final)
    if merchantMoney==nil then return end
    local money=GetMoney()
    local bags,prices=bagSnapshot()
    local sold, expected, unknown = {}, 0, false
    for itemID, originalQty in pairs(merchantBags or {}) do
        local qty=originalQty-(bags[itemID] or 0)
        if qty>0 then
            local unit=(merchantUnitPrices or {})[itemID] or vendor_price(itemID)
            if unit<=0 then unknown=true end
            sold[#sold+1]={id=itemID,qty=qty,unit=unit}
            if unit>0 then expected=expected+qty*unit end
        end
    end
    table.sort(sold,function(a,b)return a.id<b.id end)
    local gained=money-merchantMoney
    if gained>0 and #sold>0 and not unknown and expected==gained then
        for _,item in ipairs(sold) do
            eventRecord('VENDOR_SELL',item.id,item.qty,item.qty*item.unit,'confirmed',
                'Merchant wallet and bag decreases exactly matched NPC payout')
        end
        merchantMoney,merchantBags,merchantUnitPrices=money,bags,prices
    elseif final then
        if gained~=0 or #sold>0 then
            local hints={}
            for i,item in ipairs(sold) do
                if i>6 then hints[#hints+1]='...';break end
                hints[#hints+1]=tostring(item.id)..'x'..tostring(item.qty)..'@'..tostring(item.unit)
            end
            local note='Merchant net '..tostring(gained)..'c; expected '..tostring(expected)..
                'c; bag decreases ['..table.concat(hints,',')..']. Mixed sales/buys, unknown prices, or missing bag updates; verify.'
            if #sold==1 and gained>0 then
                -- Enough item evidence for a useful review, but not a reliable payout match.
                eventRecord('VENDOR_SELL',sold[1].id,sold[1].qty,gained,'review',note)
            else
                eventRecord('VENDOR_MONEY',0,0,math.max(0,gained),'review',note)
            end
        end
        merchantMoney,merchantBags,merchantUnitPrices=money,bags,prices
    end
    -- Crucially, do NOT advance the merchant baseline if the cash has arrived
    -- but the delayed bag update has not. This caused missed vendor receipts.
end

local function clearOldDebits()
    local now=GetTime()
    for i=#moneyDebits,1,-1 do
        if now-moneyDebits[i].ts>8 then table.remove(moneyDebits,i) end
    end
end

local function settleAH()
    if #pendingAH==0 then return end
    clearOldDebits()
    -- Different event names can report the same completed purchase. De-duplicate
    -- commodity success notifications and prefer auction-ID completion receipts.
    local completed={}
    for _,p in ipairs(pendingAH) do
        if p.event=='AUCTION_HOUSE_PURCHASE_COMPLETED' and p.id then completed[p.id]=true end
    end
    local relevant, commodityKeys={},{}
    for _,p in ipairs(pendingAH) do
        if not (p.event=='ITEM_PURCHASED' and completed[p.id]) then
            local isCommodity=p.event=='COMMODITY_PURCHASED' or p.event=='COMMODITY_PURCHASE_SUCCEEDED'
            local key=isCommodity and (tostring(p.id)..':'..tostring(p.qty)) or nil
            if not key or not commodityKeys[key] then
                relevant[#relevant+1]=p
                if key then commodityKeys[key]=true end
            end
        end
    end
    -- A commodity purchase knows the server's quoted total, which is exactly what leaves the
    -- wallet, so each one is matched to a debit of that amount even when several land together
    -- (a quick run of purchases) or a deposit is taken at the same time.
    local unmatched={}
    for _,p in ipairs(relevant) do
        local matched
        if p.expected and p.expected>0 and p.id and p.id>0 and (p.qty or 0)>0 then
            for i,entry in ipairs(moneyDebits) do
                if entry.amount==p.expected then
                    table.remove(moneyDebits,i)
                    matched=true
                    break
                end
            end
        end
        if matched then
            eventRecord('AH_BUY',p.id,p.qty,p.expected,'confirmed',
                'Purchase matched a wallet debit of exactly its quoted price')
        else
            unmatched[#unmatched+1]=p
        end
    end
    relevant=unmatched
    if #relevant==0 then pendingAH={} return end
    if #relevant==1 and #moneyDebits>=1 then
        local p=relevant[1]
        local debit=0
        for _,entry in ipairs(moneyDebits) do debit=debit+entry.amount end
        local valid=p.id and p.id>0 and (p.qty or 0)>0 and debit>0
        if p.expected and p.expected~=debit then valid=false end
        if valid then
            eventRecord('AH_BUY',p.id,p.qty,debit,'confirmed',
                'One purchase matched AH debit; item and quantity identified')
        else
            eventRecord('AH_BUY',p.id,p.qty,debit,'review',
                'Purchase cost/identity uncertain; verify auction mail or receipt')
        end
        moneyDebits={}
    else
        for _,p in ipairs(relevant) do
            eventRecord('AH_BUY',p.id,p.qty,p.expected or 0,'review',
                'Could not uniquely match purchase events and wallet debits')
        end
    end
    pendingAH={}
end

local function flush()
    if mode=='MERCHANT' then settleMerchant(false)
    elseif mode=='AH' then settleAH() end
    previousMoney=GetMoney()
    recordWallet()
end
local function scheduleFlush()
    flushToken=flushToken+1
    local token=flushToken
    if C_Timer and C_Timer.After then
        C_Timer.After(.85,function() if token==flushToken then flush() end end)
    else
        flush()
    end
end
local function pushPurchase(id,qty,event,expected)
    pendingAH[#pendingAH+1]={id=id or 0,qty=qty or 0,event=event,expected=expected}
    scheduleFlush()
end

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

-- Capture the item's identity from the player's own commodity-buy confirmation.
-- This never sends a query, confirms a purchase, or changes protected UI actions.
if AH and hooksecurefunc and type(AH.ConfirmCommoditiesPurchase)=='function' then
    pcall(hooksecurefunc,AH,'ConfirmCommoditiesPurchase',function(itemID,qty)
        if type(itemID)=='number' and type(qty)=='number' and qty>0 then
            -- The quote that came just before the confirm is what the server will take.
            local quote=lastQuote and GetTime()-lastQuote.ts<=60 and lastQuote.total or nil
            commodityIntent={id=itemID,qty=qty,ts=GetTime(),expected=quote}
        end
    end)
end
local function commodityPurchase(event,itemID,qty)
    local intent=commodityIntent
    local now=GetTime()
    if type(itemID)~='number' or itemID==0 then itemID=nil end
    if intent and now-intent.ts<=12 and
        (not itemID or itemID==intent.id) then
        pushPurchase(intent.id,intent.qty,event,intent.expected)
        lastCommodity={id=intent.id,ts=now}
        commodityIntent=nil
    elseif lastCommodity and now-lastCommodity.ts<=3 and (not itemID or itemID==lastCommodity.id) then
        -- The same purchase, reported again by its second event (COMMODITY_PURCHASED and
        -- COMMODITY_PURCHASE_SUCCEEDED both fire). Counting it twice made every receipt REVIEW.
        return
    else
        pushPurchase(type(itemID)=='number' and itemID or 0,
                     type(qty)=='number' and qty or 0,event)
    end
end

for _,ev in ipairs({
    'PLAYER_LOGIN','PLAYER_ENTERING_WORLD','PLAYER_MONEY','BAG_UPDATE_DELAYED',
    'AUCTION_HOUSE_SHOW','AUCTION_HOUSE_CLOSED','MERCHANT_SHOW','MERCHANT_CLOSED',
    'COMMODITY_PURCHASED','COMMODITY_PURCHASE_SUCCEEDED','ITEM_PURCHASED','COMMODITY_PRICE_UPDATED',
    'AUCTION_HOUSE_PURCHASE_COMPLETED','ITEM_SEARCH_RESULTS_UPDATED','ITEM_SEARCH_RESULTS_ADDED'
}) do listen(txnFrame, ev) end

txnFrame:SetScript('OnEvent',function(_,event,...)
    if event=='PLAYER_LOGIN' or event=='PLAYER_ENTERING_WORLD' then
        local name=UnitName('player') or 'Unknown'
        local server=GetRealmName and GetRealmName() or 'PvP'
        characterID=name..'-'..server
        previousMoney=GetMoney()
        recordWallet()
    elseif event=='AUCTION_HOUSE_SHOW' then
        if merchantMoney~=nil then settleMerchant(true) end
        merchantMoney=nil;mode='AH';pendingAH={};moneyDebits={}
        previousMoney=GetMoney();recordWallet()
    elseif event=='AUCTION_HOUSE_CLOSED' then
        scheduleFlush()
        if C_Timer and C_Timer.After then
            C_Timer.After(2,function() if mode=='AH' then settleAH();mode=nil;recordWallet() end end)
        end
    elseif event=='MERCHANT_SHOW' then
        -- The optical data link normally sleeps outside the AH. Wake it for
        -- the merchant session so verified vendor payouts reach Windows live.
        if NS.Link and NS.Link.Open then NS.Link.Open() end
        if merchantMoney~=nil then settleMerchant(true) end
        merchantToken=merchantToken+1
        mode='MERCHANT';merchantMoney=GetMoney()
        merchantBags,merchantUnitPrices=bagSnapshot()
        previousMoney=merchantMoney;recordWallet()
    elseif event=='MERCHANT_CLOSED' then
        merchantToken=merchantToken+1
        local token=merchantToken
        flushToken=flushToken+1
        mode=nil
        if C_Timer and C_Timer.After then
            C_Timer.After(.35,function()
                if token==merchantToken then
                    settleMerchant(true);merchantMoney=nil;recordWallet()
                    if NS.Link and NS.Link.Close then NS.Link.Close() end
                end
            end)
        else
            settleMerchant(true);merchantMoney=nil;recordWallet()
            if NS.Link and NS.Link.Close then NS.Link.Close() end
        end
        previousMoney=GetMoney()
    elseif event=='PLAYER_MONEY' then
        local nowMoney=GetMoney()
        if mode=='AH' and previousMoney and nowMoney<previousMoney then
            moneyDebits[#moneyDebits+1]={amount=previousMoney-nowMoney,ts=GetTime()}
        end
        previousMoney=nowMoney;recordWallet()
        if mode=='AH' or mode=='MERCHANT' then scheduleFlush() end
    elseif event=='BAG_UPDATE_DELAYED' then
        if mode=='MERCHANT' then scheduleFlush() end
    elseif event=='COMMODITY_PRICE_UPDATED' then
        local _,total=...
        if type(total)=='number' and total>0 then lastQuote={total=total,ts=GetTime()} end
    elseif event=='COMMODITY_PURCHASED' or event=='COMMODITY_PURCHASE_SUCCEEDED' then
        local itemID,qty=...
        commodityPurchase(event,itemID,qty)
    elseif event=='ITEM_PURCHASED' then
        local itemID=...
        if type(itemID)=='number' then pushPurchase(itemID,0,event) end
    elseif event=='AUCTION_HOUSE_PURCHASE_COMPLETED' then
        local auctionID=...
        local info=auctionCache[auctionID]
        local now=GetTime()
        if info then
            pushPurchase(info.id,info.qty,event,info.cost)
            auctionCache[auctionID]=nil
        elseif (lastCommodity and now-lastCommodity.ts<=3) or (commodityIntent and now-commodityIntent.ts<=12) then
            -- A commodity purchase's own completion notice: that purchase is counted by its
            -- commodity event, so this is not a second, unknown purchase.
        else
            pushPurchase(0,0,event)
        end
    elseif event=='ITEM_SEARCH_RESULTS_UPDATED' or event=='ITEM_SEARCH_RESULTS_ADDED' then
        pcall(cacheItemAuctions,...)
    end
end)

SLASH_FOREVERTRADERACTIVITY1='/ftmoney'
SlashCmdList.FOREVERTRADERACTIVITY=function()
    local db=ensureTxnDB()
    print('Forever Trader: wallet '..(GetCoinTextureString and GetCoinTextureString(GetMoney()) or tostring(GetMoney())..' copper'))
    if db then
        print('Forever Trader: '..#db.activity..' receipts, '..#db.wallet_changes..
            ' wallet checkpoints. Type /reload to sync the Windows app.')
    end
end
