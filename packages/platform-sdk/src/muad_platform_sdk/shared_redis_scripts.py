"""Redis scripts for the shared egress counter.

All keys must be supplied by the caller and share a Redis Cluster hash slot.
Scripts validate state before writing; Redis scripts do not roll back on error.
"""

PUBLISH = r"""
local guard, policy, decisions = KEYS[1], KEYS[2], KEYS[3]
local function kind(key, expected)
  local t = redis.call('TYPE', key).ok
  return t == 'none' or t == expected
end
if not kind(guard, 'hash') or not kind(policy, 'hash') or not kind(decisions, 'hash') then
  return {'CORRUPT'}
end
if redis.call('HGET', guard, 'epoch') ~= ARGV[1] then return {'EPOCH'} end
local old_revision = tonumber(redis.call('HGET', policy, 'revision') or '-1')
local revision = tonumber(ARGV[2])
if redis.call('HGET', guard, 'initialized') == '1' and old_revision == -1 then return {'CORRUPT'} end
if redis.call('HGET', guard, 'initialized') == '1' and
   redis.call('HGET', decisions, '__guard__') ~= ARGV[1] then return {'CORRUPT'} end
if not revision or revision < old_revision then return {'REVISION'} end
local old_json = redis.call('HGET', policy, 'json')
if revision == old_revision then
  if old_json ~= ARGV[3] then return {'REVISION'} end
  return {'OK'}
end
local now = redis.call('TIME')
local ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local last = tonumber(redis.call('HGET', guard, 'last_ms') or '0')
if ms < last then return {'CLOCK'} end
local old_rules = old_json and cjson.decode(old_json).rules or {}
local new_rules = cjson.decode(ARGV[3]).rules
if #KEYS ~= 3 + 3 * #new_rules then return {'INVALID'} end
local previous = {}
for _, rule in ipairs(old_rules) do previous[rule.id] = rule end
for i, rule in ipairs(new_rules) do
  local old = previous[rule.id]
  local identity = cjson.encode({rule.target_instance_id, rule.route_id})
  local stored_identity = redis.call('HGET', policy, 'identity:' .. rule.id)
  if stored_identity and stored_identity ~= identity then
    return {'IDENTITY'}
  end
  for j = 0, 2 do
    local t = redis.call('TYPE', KEYS[3 * i + j + 1]).ok
    local expected = j == 0 and 'string' or 'zset'
    if (stored_identity and t ~= expected) or (not stored_identity and t ~= 'none') then
      return {'CORRUPT'}
    end
    if stored_identity and j > 0 and not redis.call('ZSCORE', KEYS[3 * i + j + 1], '__guard__') then
      return {'CORRUPT'}
    end
  end
end
-- Block newly introduced or lengthened windows until a complete history exists.
for i, rule in ipairs(new_rules) do
  local old = previous[rule.id]
  if not redis.call('HGET', policy, 'identity:' .. rule.id) then
    redis.call('HSET', policy, 'identity:' .. rule.id, cjson.encode({rule.target_instance_id, rule.route_id}))
    redis.call('SET', KEYS[3 * i + 1], '0')
    redis.call('ZADD', KEYS[3 * i + 2], -1, '__guard__')
    redis.call('ZADD', KEYS[3 * i + 3], -1, '__guard__')
  end
  if rule.window_seconds ~= cjson.null and rule.window_seconds and
     (not old or old.window_seconds == cjson.null or not old.window_seconds or
      rule.window_seconds > old.window_seconds) then
    redis.call('HSET', policy, 'blocked:' .. rule.id, ms + math.ceil(rule.window_seconds * 1000))
  end
end
redis.call('HSET', guard, 'last_ms', ms)
redis.call('HSET', decisions, '__guard__', ARGV[1])
redis.call('HSET', guard, 'initialized', '1')
redis.call('HSET', policy, 'revision', ARGV[2], 'json', ARGV[3])
return {'OK'}
"""

ADMIT = r"""
local guard, policy, decision = KEYS[1], KEYS[2], KEYS[3]
local function kind(key, expected)
  local t = redis.call('TYPE', key).ok
  return t == 'none' or t == expected
end
if not kind(guard, 'hash') or not kind(policy, 'hash') or not kind(decision, 'hash') then
  return {'CORRUPT'}
end
if redis.call('HGET', guard, 'epoch') ~= ARGV[1] then return {'EPOCH'} end
if redis.call('HGET', decision, '__guard__') ~= ARGV[1] then return {'CORRUPT'} end
if redis.call('HGET', policy, 'json') ~= ARGV[2] then return {'POLICY'} end
local rules = cjson.decode(ARGV[5])
local recorded = redis.call('HGET', decision, ARGV[3])
if recorded then
  if recorded ~= ARGV[4] then return {'ATTEMPT_CONFLICT'} end
  local expiry = redis.call('HGET', decision, '__expiry:' .. ARGV[3])
  if not expiry then return {'CORRUPT'} end
  return {'DUPLICATE', expiry}
end
if #rules == 0 then return {'UNCONTROLLED'} end
local now = redis.call('TIME')
local ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local last = tonumber(redis.call('HGET', guard, 'last_ms') or '0')
if ms < last then return {'CLOCK'} end
local lease_ms = tonumber(ARGV[6])
if not lease_ms or lease_ms <= 0 or #KEYS ~= 3 + #rules * 3 then return {'INVALID'} end
for i, rule in ipairs(rules) do
  local total, window, leases = KEYS[3 * i + 1], KEYS[3 * i + 2], KEYS[3 * i + 3]
  if redis.call('TYPE', total).ok ~= 'string' or redis.call('TYPE', window).ok ~= 'zset'
     or redis.call('TYPE', leases).ok ~= 'zset' or not redis.call('ZSCORE', window, '__guard__')
     or not redis.call('ZSCORE', leases, '__guard__') then
    return {'CORRUPT'}
  end
  local used = tonumber(redis.call('GET', total) or '0')
  if not used or used < 0 or used >= 9007199254740991 or used ~= math.floor(used) then return {'CORRUPT'} end
  if rule.max_requests ~= cjson.null and rule.max_requests and used >= rule.max_requests then
    return {'TOTAL', rule.id, tostring(used)}
  end
  if rule.window_seconds ~= cjson.null and rule.window_seconds then
    local blocked = tonumber(redis.call('HGET', policy, 'blocked:' .. rule.id) or '0')
    if ms < blocked then return {'WINDOW', rule.id, tostring(blocked - ms)} end
    local cutoff = ms - math.ceil(rule.window_seconds * 1000)
    local count = redis.call('ZCOUNT', window, '(' .. cutoff, '+inf')
    if count >= rule.window_requests then return {'WINDOW', rule.id, '0'} end
  end
  if rule.max_inflight ~= cjson.null and rule.max_inflight then
    if redis.call('ZCOUNT', leases, '(' .. ms, '+inf') >= rule.max_inflight then
      return {'INFLIGHT', rule.id, '0'}
    end
  end
end
redis.call('HSET', guard, 'last_ms', ms)
for i, rule in ipairs(rules) do
  local total, window, leases = KEYS[3 * i + 1], KEYS[3 * i + 2], KEYS[3 * i + 3]
  redis.call('INCR', total)
  if rule.window_seconds ~= cjson.null and rule.window_seconds then
    local cutoff = ms - math.ceil(rule.window_seconds * 1000)
    redis.call('ZREMRANGEBYSCORE', window, '0', cutoff)
    redis.call('ZADD', window, ms, ARGV[3])
  end
  if rule.max_inflight ~= cjson.null and rule.max_inflight then
    redis.call('ZREMRANGEBYSCORE', leases, '0', ms)
    redis.call('ZADD', leases, ms + lease_ms, ARGV[3])
  end
end
redis.call('HSET', decision, ARGV[3], ARGV[4])
redis.call('HSET', decision, '__expiry:' .. ARGV[3], tostring(ms + lease_ms))
return {'OK', tostring(ms + lease_ms)}
"""

RELEASE = r"""
local decision = KEYS[1]
if redis.call('TYPE', decision).ok ~= 'hash' then return {'CORRUPT'} end
if redis.call('HGET', decision, ARGV[1]) ~= ARGV[2] then return {'UNKNOWN'} end
for i = 2, #KEYS do
  local t = redis.call('TYPE', KEYS[i]).ok
  if t ~= 'none' and t ~= 'zset' then return {'CORRUPT'} end
end
for i = 2, #KEYS do redis.call('ZREM', KEYS[i], ARGV[1]) end
redis.call('HSET', decision, '__released:' .. ARGV[1], '1')
return {'OK'}
"""

CONSUME = r"""
local guard, decision = KEYS[1], KEYS[2]
if redis.call('TYPE', guard).ok ~= 'hash' or redis.call('TYPE', decision).ok ~= 'hash' then
  return {'CORRUPT'}
end
if redis.call('HGET', guard, 'epoch') ~= ARGV[1] or
   redis.call('HGET', decision, '__guard__') ~= ARGV[1] then return {'EPOCH'} end
if redis.call('HGET', decision, ARGV[2]) ~= ARGV[3] then return {'UNKNOWN'} end
local expiry = tonumber(redis.call('HGET', decision, '__expiry:' .. ARGV[2]) or '')
if not expiry then return {'CORRUPT'} end
local now = redis.call('TIME')
local ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local last = tonumber(redis.call('HGET', guard, 'last_ms') or '0')
if ms < last then return {'CLOCK'} end
if ms >= expiry or redis.call('HEXISTS', decision, '__released:' .. ARGV[2]) == 1 then
  return {'EXPIRED'}
end
if redis.call('HEXISTS', decision, '__consumed:' .. ARGV[2]) == 1 then return {'CONSUMED'} end
redis.call('HSET', guard, 'last_ms', ms)
redis.call('HSET', decision, '__consumed:' .. ARGV[2], '1')
return {'OK'}
"""
