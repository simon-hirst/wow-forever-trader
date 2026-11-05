-- Plan: per-item buy limits from the desktop app's market engine.
--
-- The app writes the same plan into FTPlan01..FTPlan24, small load-on-demand
-- addons. Each can be loaded once per UI session and the game reads its file at
-- that moment, so loading the next slot picks up a newer plan with no /reload.
local _, NS = ...
local Plan = {items = {}, rev = 0, ts = 0, slot = 0, reserve = nil}
NS.Plan = Plan

local SLOTS = 24
local REFRESH_SECONDS = 600
local MAX_AGE_SECONDS = 24 * 60 * 60

local nextLoad = 0
local missing = false

-- Called from the plan file itself. Item entries: l=limit, q=max units, t=resale
-- target, f=fair price, g=grade.
function ForeverTrader_ReceivePlan(plan)
    if type(plan) ~= 'table' or type(plan.items) ~= 'table' then return end
    local rev, ts = tonumber(plan.rev) or 0, tonumber(plan.ts) or 0
    if rev <= Plan.rev then return end
    local items, count = {}, 0
    for id, entry in pairs(plan.items) do
        local limit = type(entry) == 'table' and tonumber(entry.l)
        if type(id) == 'number' and limit and limit > 0 then
            items[id] = {l = limit, q = tonumber(entry.q) or 0, t = tonumber(entry.t) or 0,
                         f = tonumber(entry.f) or 0, g = entry.g}
            count = count + 1
        end
    end
    Plan.items, Plan.rev, Plan.ts, Plan.count = items, rev, ts, count
    Plan.reserve = tonumber(plan.reserve)
    Plan.spec = tonumber(plan.spec)
    NS.Log(string.format('plan rev %d loaded: %d buy limits', rev, count))
    if NS.Aux then NS.Aux.PlanChanged() end
    NS.Link.MarkState()
end

function Plan.For(itemID)
    local entry = Plan.items[itemID]
    if not entry then return nil end
    if NS.Now() - Plan.ts > MAX_AGE_SECONDS and not entry.test then return nil end
    return entry
end

-- /ft testplan: one temporary limit for proving the purchase path with a small buy.
function Plan.SetTest(itemID, limit, units)
    Plan.items[itemID] = {l = limit, q = units, t = limit, f = limit, test = true}
    if NS.Aux then NS.Aux.PlanChanged() end
end

function Plan.LoadNext()
    if missing or Plan.slot >= SLOTS then return end
    local load = C_AddOns and C_AddOns.LoadAddOn or _G.LoadAddOn
    if not load then return end
    Plan.slot = Plan.slot + 1
    local name = string.format('FTPlan%02d', Plan.slot)
    local ok, reason = load(name)
    if not ok and (reason == 'MISSING' or reason == 'DISABLED') then
        -- Slots appear after the app installs them and the game restarts.
        missing = true
        NS.Log('plan slots unavailable: ' .. tostring(reason))
    end
end

function Plan.Tick()
    if NS.AuctionHouseOpen and GetTime() >= nextLoad then
        nextLoad = GetTime() + REFRESH_SECONDS
        Plan.LoadNext()
    end
end
