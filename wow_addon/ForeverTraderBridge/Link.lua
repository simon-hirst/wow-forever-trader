-- Link: one-way data channel from the addon to the desktop app.
--
-- An addon cannot write a file while the game runs, and C_UI.Reload needs a real
-- key press, so nothing reaches disk while the auction house stays open. Instead a
-- strip of coloured blocks is drawn along the top edge of the game window and the
-- desktop app reads those pixels. Reading pixels touches nothing in the game.
--
-- Strip: 256 blocks, 3x3 physical pixels each (768x3). The first 8 blocks are a
-- grey ramp the reader calibrates against; each data block carries 9 bits (3 bits
-- per colour channel).
--
-- Frame bytes: [0x10|type][seq][len lo][len hi][payload...][sum1][sum2][seq]
-- trader/link.py is the matching decoder; change them together.
local _, NS = ...
local Link = {}
NS.Link = Link

local floor = math.floor

local BLOCK_PX, BLOCKS, RAMP = 3, 256, 8
local DATA_BLOCKS = BLOCKS - RAMP
local MAX_BYTES = floor(DATA_BLOCKS * 9 / 8)
local OVERHEAD = 7
local VERSION = 1
local HOLD_SECONDS = 1 / 15
local STATE_SECONDS = 0.5
local MAX_QUEUE = 8000
local DRAIN_SECONDS = 120

Link.MAX_PAYLOAD = MAX_BYTES - OVERHEAD
Link.TYPE = {STATE = 0, BARS = 1, L3 = 2, LOG = 3, L3_END = 4, TEST = 5}

local strip, blocks
local active, closingAt = false, nil
local seq = 0
local queue, head, tail = {}, 1, 0
-- Critical trade receipts must jump ahead of full-market listing chunks.
-- Without a second queue, a vendor sale can wait behind minutes of AH
-- snapshot traffic and then seem to "appear only after /reload".
local urgentQueue, urgentHead, urgentTail = {}, 1, 0
local framesSince, elapsedSince = 0, 0
local stateDirty, nextState = true, 0
Link.sent = 0
Link.droppedUrgent = 0

local function queued()
    return tail - head + 1 + urgentTail - urgentHead + 1
end

local function createStrip()
    if strip then return end
    strip = CreateFrame('Frame', 'ForeverTraderLinkStrip', nil)
    strip:SetFrameStrata('TOOLTIP')
    strip:SetFrameLevel(9000)
    strip:EnableMouse(false)
    blocks = {}
    for i = 1, BLOCKS do
        local t = strip:CreateTexture(nil, 'OVERLAY')
        t:SetSize(BLOCK_PX, BLOCK_PX)
        t:SetPoint('TOPLEFT', strip, 'TOPLEFT', (i - 1) * BLOCK_PX, 0)
        if t.SetSnapToPixelGrid then t:SetSnapToPixelGrid(false) end
        if t.SetTexelSnappingBias then t:SetTexelSnappingBias(0) end
        if i <= RAMP then
            local v = (i - 1) / 7
            t:SetColorTexture(v, v, v, 1)
        else
            t:SetColorTexture(0, 0, 0, 1)
        end
        blocks[i] = t
    end
    strip:Hide()
end

-- One UI unit equals one physical pixel whatever the player's UI scale is.
local function layoutStrip()
    createStrip()
    local _, height = GetPhysicalScreenSize()
    if not height or height <= 0 then height = 1080 end
    strip:SetScale(768 / height)
    strip:SetSize(BLOCK_PX * BLOCKS, BLOCK_PX)
    strip:ClearAllPoints()
    strip:SetPoint('TOPLEFT', UIParent, 'TOPLEFT', 0, 0)
end

-- Pure so it can be tested outside the game: payload string to frame byte array.
function Link.BuildFrame(ftype, frameSeq, payload)
    local n = #payload
    local b = {16 * VERSION + ftype, frameSeq, n % 256, floor(n / 256)}
    for i = 1, n do b[4 + i] = string.byte(payload, i) end
    local s1, s2 = 0, 0
    for i = 1, 4 + n do
        s1 = (s1 + b[i]) % 251
        s2 = (s2 + s1) % 251
    end
    b[5 + n], b[6 + n], b[7 + n] = s1, s2, frameSeq
    for i = 8 + n, MAX_BYTES do b[i] = 0 end
    return b
end

-- Pure: frame byte array to one 9-bit value per data block.
function Link.ToBlocks(bytes)
    local out, acc, bits, bi = {}, 0, 0, 1
    for i = 1, DATA_BLOCKS do
        while bits < 9 do
            acc = acc * 256 + (bytes[bi] or 0)
            bi = bi + 1
            bits = bits + 8
        end
        bits = bits - 9
        local divisor = 2 ^ bits
        local v = floor(acc / divisor)
        acc = acc - v * divisor
        out[i] = v
    end
    return out
end

local function paint(ftype, payload)
    seq = (seq + 1) % 256
    local values = Link.ToBlocks(Link.BuildFrame(ftype, seq, payload))
    for i = 1, DATA_BLOCKS do
        local v = values[i]
        blocks[RAMP + i]:SetColorTexture(floor(v / 64) / 7, (floor(v / 8) % 8) / 7, (v % 8) / 7, 1)
    end
    Link.sent = Link.sent + 1
end

function Link.Enqueue(ftype, payload)
    if #payload > Link.MAX_PAYLOAD then return false end
    local urgent = ftype == Link.TYPE.LOG and
        (string.sub(payload,1,7) == 'FTREC1|' or string.sub(payload,1,6) == 'FTPM1|')
    if queued() >= MAX_QUEUE then
        if head <= tail then
            -- Sacrifice a low-priority market chunk before a trade receipt.
            -- The saved-variable mirror can recover ordinary snapshot data.
            queue[head] = nil
            head = head + 1
        elseif not urgent then
            return false
        else
            -- This only occurs if thousands of unsent receipts accumulate
            -- while the game is not visible. They still persist to disk.
            urgentQueue[urgentHead] = nil
            urgentHead = urgentHead + 1
            Link.droppedUrgent = Link.droppedUrgent + 1
        end
    end
    if urgent then
        urgentTail = urgentTail + 1
        urgentQueue[urgentTail] = {ftype, payload}
    else
        tail = tail + 1
        queue[tail] = {ftype, payload}
    end
    return true
end

function Link.QueueLength()
    return queued()
end

function Link.MarkState()
    stateDirty = true
end

function Link.Open()
    if not NS.Settings().link then return end
    layoutStrip()
    active = true
    closingAt = nil
    stateDirty = true
    strip:Show()
end

-- Keep going after the auction house closes so queued data still gets across.
function Link.Close()
    if active then closingAt = GetTime() + DRAIN_SECONDS end
    stateDirty = true
end

function Link.Stop()
    active = false
    closingAt = nil
    if strip then strip:Hide() end
end

function Link.IsActive()
    return active
end

function Link.Tick(elapsed)
    if not active then return end
    framesSince = framesSince + 1
    elapsedSince = elapsedSince + elapsed
    -- Hold each frame for two rendered frames so the reader cannot miss or tear it.
    if framesSince < 2 or elapsedSince < HOLD_SECONDS then return end
    local now = GetTime()
    if closingAt and (now >= closingAt or queued() == 0) then
        Link.Stop()
        return
    end
    local ftype, payload
    if stateDirty or now >= nextState then
        ftype, payload = Link.TYPE.STATE, NS.BuildState()
        stateDirty, nextState = false, now + STATE_SECONDS
    elseif urgentHead <= urgentTail then
        local entry = urgentQueue[urgentHead]
        urgentQueue[urgentHead] = nil
        urgentHead = urgentHead + 1
        ftype, payload = entry[1], entry[2]
    elseif head <= tail then
        local entry = queue[head]
        queue[head] = nil
        head = head + 1
        ftype, payload = entry[1], entry[2]
    else
        return
    end
    framesSince, elapsedSince = 0, 0
    paint(ftype, payload)
end

-- Little-endian base-128 varints. Doubles are exact to 2^53, and prices can be
-- absurdly large, so this stays arithmetic rather than using 32-bit bit ops.
function Link.PutVarint(t, value)
    local n = floor(tonumber(value) or 0)
    if n < 0 then n = 0 end
    while n >= 128 do
        t[#t + 1] = n % 128 + 128
        n = floor(n / 128)
    end
    t[#t + 1] = n
end

local function bytesToString(t)
    local parts, n = {}, #t
    for i = 1, n, 4000 do
        parts[#parts + 1] = string.char(unpack(t, i, math.min(i + 3999, n)))
    end
    return table.concat(parts)
end
Link.BytesToString = bytesToString

function Link.Log(text)
    Link.Enqueue(Link.TYPE.LOG, string.sub(tostring(text), 1, Link.MAX_PAYLOAD))
end

-- Known payloads so the app can measure colour fidelity and frame loss.
function Link.TestBurst(count)
    for i = 1, count do
        local t = {}
        Link.PutVarint(t, i)
        Link.PutVarint(t, count)
        for k = #t + 1, Link.MAX_PAYLOAD do t[k] = (i * 7 + k * 13) % 256 end
        Link.Enqueue(Link.TYPE.TEST, bytesToString(t))
    end
end
