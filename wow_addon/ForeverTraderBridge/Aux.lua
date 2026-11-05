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
    env.check_list = function(results, seen)
        local ok, err = pcall(NS.Market.OnBrowse, results)
        if not ok then NS.Log('market error: ' .. tostring(err)) end
        return checkList(results, seen)
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
