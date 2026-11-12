"""Redis scripts for the shared egress counter.

All keys must be supplied by the caller and share a Redis Cluster hash slot.
Scripts validate state before writing; Redis scripts do not roll back on error.
"""

_SERVER_GENERATION = r"""
local function server_generation(guard, initialize)
  local info = redis.pcall('INFO', 'server')
  if type(info) ~= 'string' then return false end
  local run_id = string.match(info, 'run_id:([^\r\n]+)')
  local mode = string.match(info, 'redis_mode:([^\r\n]+)')
  if not run_id or #run_id ~= 40 then return false end
  local generation = run_id
  if mode == 'cluster' then
    local topology = redis.pcall('CLUSTER', 'INFO')
    if type(topology) ~= 'string' or
       string.match(topology, 'cluster_state:([^\r\n]+)') ~= 'ok' then return false end
    local epoch = string.match(topology, 'cluster_my_epoch:(%d+)')
    if not epoch then return false end
    generation = generation .. ':' .. epoch
  elseif mode ~= 'standalone' then
    return false
  end
  local pinned = redis.call('HGET', guard, 'server_generation')
  if not pinned then
    return initialize and redis.call('HGET', guard, 'initialized') ~= '1', generation
  end
  return pinned == generation, generation
end
"""

REGISTER_SCOPE = r"""
local guard, index, scope, parent = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
if ARGV[7] ~= 'create' and ARGV[7] ~= 'resume' then return {'INVALID'} end
local function kind(key, expected)
  local t = redis.call('TYPE', key).ok
  return t == 'none' or t == expected
end
if redis.call('TYPE', guard).ok ~= 'hash' or not kind(index, 'hash') or not kind(scope, 'hash') then
  return {'CORRUPT'}
end
if redis.call('HGET', guard, 'epoch') ~= ARGV[1] then return {'EPOCH'} end
local generation_ok, generation = server_generation(guard, false)
if not generation_ok then return {'SERVER_GENERATION'} end
if redis.call('HGET', guard, 'initialized') ~= '1' then return {'UNINITIALIZED'} end
if redis.call('HGET', guard, 'scopes_initialized') == '1' and
   redis.call('HGET', index, '__guard__') ~= ARGV[1] then return {'CORRUPT'} end
if ARGV[5] ~= '' and (redis.call('TYPE', parent).ok ~= 'hash' or
   redis.call('HGET', parent, 'identity') ~= ARGV[5] or
   redis.call('HGET', index, ARGV[6]) ~= ARGV[5] or
   not redis.call('HGET', index, 'limit:' .. ARGV[6]) or
   redis.call('HGET', parent, 'limit') ~= redis.call('HGET', index, 'limit:' .. ARGV[6])) then
  return {'PARENT'}
end
local limit = tonumber(ARGV[4])
if ARGV[4] ~= 'none' and
   (not limit or limit <= 0 or limit ~= math.floor(limit) or limit >= 9007199254740991) then
  return {'INVALID'}
end
local registered = redis.call('HGET', index, ARGV[2])
local targets = cjson.decode(ARGV[8])
local seen = {}
for _, target in ipairs(targets) do
  local maximum = tonumber(target.max_requests)
  if type(target.id) ~= 'string' or seen[target.id] or not maximum or maximum <= 0 or
     maximum >= 9007199254740991 or maximum ~= math.floor(maximum) then return {'INVALID'} end
  seen[target.id] = true
end
if registered then
  if registered ~= ARGV[3] or redis.call('HGET', scope, 'identity') ~= ARGV[3] or
     redis.call('HGET', scope, 'limit') ~= ARGV[4] or
     redis.call('HGET', index, 'limit:' .. ARGV[2]) ~= ARGV[4] or
     not redis.call('HGET', scope, 'used') then return {'CORRUPT'} end
  if redis.call('HGET', scope, 'targets') ~= ARGV[8] or
     redis.call('HGET', index, 'targets:' .. ARGV[2]) ~= ARGV[8] then return {'CORRUPT'} end
  local raw = redis.call('HGET', scope, 'used')
  local used = tonumber(raw)
  if not used or used < 0 or used >= 9007199254740991 or
     raw ~= string.format('%.0f', used) then return {'CORRUPT'} end
  for _, target in ipairs(targets) do
    local raw_target = redis.call('HGET', scope, 'target:' .. target.id)
    local count = tonumber(raw_target or '')
    if not count or count < 0 or count >= 9007199254740991 or
       raw_target ~= string.format('%.0f', count) then return {'CORRUPT'} end
  end
  return {'OK'}
end
if ARGV[7] == 'resume' then return {'MISSING_SCOPE'} end
if redis.call('TYPE', scope).ok ~= 'none' or redis.call('HEXISTS', index, 'limit:' .. ARGV[2]) == 1 or
   redis.call('HEXISTS', index, 'targets:' .. ARGV[2]) == 1 then
  return {'CORRUPT'}
end
redis.call('HSET', index, '__guard__', ARGV[1], ARGV[2], ARGV[3], 'limit:' .. ARGV[2], ARGV[4])
redis.call('HSET', scope, 'identity', ARGV[3], 'limit', ARGV[4], 'used', '0')
redis.call('HSET', scope, 'targets', ARGV[8])
redis.call('HSET', index, 'targets:' .. ARGV[2], ARGV[8])
for _, target in ipairs(targets) do redis.call('HSET', scope, 'target:' .. target.id, '0') end
redis.call('HSET', guard, 'scopes_initialized', '1')
return {'OK'}
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
local generation_ok, generation = server_generation(guard, true)
if not generation_ok then return {'SERVER_GENERATION'} end
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
for i, rule in ipairs(new_rules) do
  if not redis.call('HGET', policy, 'identity:' .. rule.id) then
    redis.call('HSET', policy, 'identity:' .. rule.id, cjson.encode({rule.target_instance_id, rule.route_id}))
    redis.call('SET', KEYS[3 * i + 1], '0')
    redis.call('ZADD', KEYS[3 * i + 2], -1, '__guard__')
    redis.call('ZADD', KEYS[3 * i + 3], -1, '__guard__')
  end
end
redis.call('HSET', guard, 'last_ms', ms)
redis.call('HSET', decisions, '__guard__', ARGV[1])
redis.call('HSET', guard, 'server_generation', generation)
redis.call('HSET', guard, 'initialized', '1')
redis.call('HSET', policy, 'revision', ARGV[2], 'json', ARGV[3])
return {'OK'}
"""

READ_POLICY = r"""
local guard, policy, decisions = KEYS[1], KEYS[2], KEYS[3]
for _, key in ipairs(KEYS) do
  if redis.call('TYPE', key).ok ~= 'hash' then return {'CORRUPT'} end
end
if redis.call('HGET', guard, 'epoch') ~= ARGV[1] then return {'EPOCH'} end
if redis.call('HGET', guard, 'initialized') ~= '1' or
   redis.call('HGET', decisions, '__guard__') ~= ARGV[1] then return {'CORRUPT'} end
local generation_ok = server_generation(guard, false)
if not generation_ok then return {'SERVER_GENERATION'} end
local now = redis.call('TIME')
local ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local last_ms = tonumber(redis.call('HGET', guard, 'last_ms') or '')
if not last_ms or ms < last_ms then return {'CLOCK'} end
local raw = redis.call('HGET', policy, 'json')
local revision = redis.call('HGET', policy, 'revision')
local version = tonumber(revision or '')
if not raw or not version or version < 0 or version >= 9007199254740991 or
   revision ~= string.format('%.0f', version) then return {'CORRUPT'} end
return {'OK', revision, raw}
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
local generation_ok, generation = server_generation(guard, false)
if not generation_ok then return {'SERVER_GENERATION'} end
if redis.call('HGET', decision, '__guard__') ~= ARGV[1] then return {'CORRUPT'} end
if redis.call('HGET', policy, 'json') ~= ARGV[2] then return {'POLICY'} end
local rules = cjson.decode(ARGV[5])
local scopes = cjson.decode(ARGV[7])
local frozen_targets = {}
local index_offset = #scopes > 0 and 1 or 0
if #KEYS ~= 3 + #rules * 3 + #scopes + index_offset then return {'INVALID'} end
if #scopes > 0 and (redis.call('TYPE', KEYS[4 + #rules * 3]).ok ~= 'hash' or
   redis.call('HGET', KEYS[4 + #rules * 3], '__guard__') ~= ARGV[1]) then return {'CORRUPT'} end
for i, scope in ipairs(scopes) do
  local key = KEYS[4 + #rules * 3 + i]
  if redis.call('HGET', KEYS[4 + #rules * 3], scope.name) ~= scope.identity or
     redis.call('TYPE', key).ok ~= 'hash' or redis.call('HGET', key, 'identity') ~= scope.identity or
     not redis.call('HGET', KEYS[4 + #rules * 3], 'limit:' .. scope.name) or
     redis.call('HGET', key, 'limit') ~= redis.call('HGET', KEYS[4 + #rules * 3], 'limit:' .. scope.name) then
    return {'CORRUPT'}
  end
  local raw_used = redis.call('HGET', key, 'used')
  local used = tonumber(raw_used or '')
  local raw_limit = redis.call('HGET', key, 'limit')
  local limit = tonumber(raw_limit or '')
  if not used or used < 0 or used >= 9007199254740991 or
     used ~= math.floor(used) or raw_used ~= string.format('%.0f', used) or
     (raw_limit ~= 'none' and (not limit or limit <= 0 or limit ~= math.floor(limit))) then
    return {'CORRUPT'}
  end
  local targets_json = redis.call('HGET', key, 'targets')
  if not targets_json or targets_json ~=
     redis.call('HGET', KEYS[4 + #rules * 3], 'targets:' .. scope.name) then return {'CORRUPT'} end
  for _, target in ipairs(cjson.decode(targets_json)) do
    local raw = redis.call('HGET', key, 'target:' .. target.id)
    local count = tonumber(raw or '')
    if not count or count < 0 or count >= 9007199254740991 or
       raw ~= string.format('%.0f', count) then return {'CORRUPT'} end
    if target.target_instance_id == ARGV[8] and target.route_id == ARGV[9] then
      table.insert(frozen_targets, {key=key, id=scope.name .. ':' .. target.id,
        field='target:' .. target.id, used=count, limit=target.max_requests,
        scope=i == 1 and 'execution' or 'invocation'})
    end
  end
end
local recorded = redis.call('HGET', decision, ARGV[3])
if recorded then
  if recorded ~= ARGV[4] then return {'ATTEMPT_CONFLICT'} end
  local expiry = redis.call('HGET', decision, '__expiry:' .. ARGV[3])
  if not expiry then return {'CORRUPT'} end
  return {'DUPLICATE', expiry}
end
if #rules == 0 and #scopes == 0 then return {'UNCONTROLLED'} end
local now = redis.call('TIME')
local ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local last = tonumber(redis.call('HGET', guard, 'last_ms') or '0')
if ms < last then return {'CLOCK'} end
local lease_ms = tonumber(ARGV[6])
if not lease_ms or lease_ms <= 0 then return {'INVALID'} end
for i, rule in ipairs(rules) do
  local total, window, leases = KEYS[3 * i + 1], KEYS[3 * i + 2], KEYS[3 * i + 3]
  if redis.call('TYPE', total).ok ~= 'string' or redis.call('TYPE', window).ok ~= 'zset'
     or redis.call('TYPE', leases).ok ~= 'zset' or not redis.call('ZSCORE', window, '__guard__')
     or not redis.call('ZSCORE', leases, '__guard__') then
    return {'CORRUPT'}
  end
  local raw_used = redis.call('GET', total)
  local used = tonumber(raw_used or '')
  if not used or used < 0 or used >= 9007199254740991 or used ~= math.floor(used) or
     raw_used ~= string.format('%.0f', used) then return {'CORRUPT'} end
  if rule.max_requests ~= cjson.null and rule.max_requests and used >= rule.max_requests then
    return {'TOTAL', rule.id, tostring(used), 'public'}
  end
  if rule.window_seconds ~= cjson.null and rule.window_seconds then
    local cutoff = ms - math.ceil(rule.window_seconds * 1000)
    local count = redis.call('ZCOUNT', window, '(' .. cutoff, '+inf')
    if count >= rule.window_requests then return {'WINDOW', rule.id, '0', 'public'} end
  end
  if rule.max_inflight ~= cjson.null and rule.max_inflight then
    if redis.call('ZCOUNT', leases, '(' .. ms, '+inf') >= rule.max_inflight then
      return {'INFLIGHT', rule.id, '0', 'public'}
    end
  end
end
for i, scope in ipairs(scopes) do
  local key = KEYS[4 + #rules * 3 + i]
  local used = tonumber(redis.call('HGET', key, 'used') or '')
  local raw_limit = redis.call('HGET', key, 'limit')
  local limit = tonumber(raw_limit or '')
  if raw_limit ~= 'none' and used >= limit then
    return {'TOTAL', scope.name, tostring(used), i == 1 and 'execution' or 'invocation'}
  end
end
for _, target in ipairs(frozen_targets) do
  if target.used >= target.limit then return {'TOTAL', target.id, tostring(target.used), target.scope} end
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
for i, scope in ipairs(scopes) do
  local key = KEYS[4 + #rules * 3 + i]
  if redis.call('HGET', key, 'limit') ~= 'none' then redis.call('HINCRBY', key, 'used', 1) end
end
redis.call('HSET', decision, ARGV[3], ARGV[4])
for _, target in ipairs(frozen_targets) do redis.call('HINCRBY', target.key, target.field, 1) end
local lease_keys = {}
for i, rule in ipairs(rules) do
  if rule.max_inflight ~= cjson.null and rule.max_inflight then
    table.insert(lease_keys, KEYS[3 * i + 3])
  end
end
redis.call('HSET', decision, '__leases:' .. ARGV[3], cjson.encode(lease_keys))
redis.call('HSET', decision, '__lease_expiry:' .. ARGV[3], tostring(ms + lease_ms))
redis.call('HSET', decision, '__expiry:' .. ARGV[3], tostring(ms + lease_ms))
return {'OK', tostring(ms + lease_ms)}
"""

RELEASE = r"""
local decision = KEYS[1]
if redis.call('TYPE', decision).ok ~= 'hash' then return {'CORRUPT'} end
if redis.call('HGET', decision, ARGV[1]) ~= ARGV[2] then return {'UNKNOWN'} end
local raw_keys = redis.call('HGET', decision, '__leases:' .. ARGV[1])
if not raw_keys then return {'CORRUPT'} end
local ok, lease_keys = pcall(cjson.decode, raw_keys)
if not ok or type(lease_keys) ~= 'table' or #lease_keys ~= #KEYS - 1 then return {'CORRUPT'} end
for i = 2, #KEYS do
  if lease_keys[i - 1] ~= KEYS[i] then return {'UNKNOWN'} end
end
for i = 2, #KEYS do
  local t = redis.call('TYPE', KEYS[i]).ok
  if t ~= 'none' and t ~= 'zset' then return {'CORRUPT'} end
end
for i = 2, #KEYS do redis.call('ZREM', KEYS[i], ARGV[1]) end
redis.call('HSET', decision, '__released:' .. ARGV[1], '1')
return {'OK'}
"""

RENEW = r"""
local decision, guard = KEYS[1], KEYS[2]
if redis.call('TYPE', decision).ok ~= 'hash' or redis.call('TYPE', guard).ok ~= 'hash' then
  return {'CORRUPT'}
end
if redis.call('HGET', guard, 'epoch') ~= ARGV[4] or
   redis.call('HGET', decision, '__guard__') ~= ARGV[4] then return {'EPOCH'} end
if not server_generation(guard, false) then return {'SERVER_GENERATION'} end
if redis.call('HGET', decision, ARGV[1]) ~= ARGV[2] then return {'UNKNOWN'} end
if redis.call('HEXISTS', decision, '__released:' .. ARGV[1]) == 1 then return {'EXPIRED'} end
if redis.call('HGET', decision, '__consumed:' .. ARGV[1]) ~= '1' then return {'UNCONSUMED'} end
local lease_ms = tonumber(ARGV[3])
if not lease_ms or lease_ms <= 0 or lease_ms ~= math.floor(lease_ms) or
   lease_ms >= 9007199254740991 then return {'INVALID'} end
local now = redis.call('TIME')
local ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local expiry = tonumber(redis.call('HGET', decision, '__lease_expiry:' .. ARGV[1]) or '')
if not expiry then return {'CORRUPT'} end
if ms >= expiry then return {'EXPIRED'} end
local last = tonumber(redis.call('HGET', guard, 'last_ms') or '')
if not last then return {'CORRUPT'} end
if ms < last then return {'CLOCK'} end
local raw_keys = redis.call('HGET', decision, '__leases:' .. ARGV[1])
if not raw_keys then return {'CORRUPT'} end
local ok, lease_keys = pcall(cjson.decode, raw_keys)
if not ok or type(lease_keys) ~= 'table' or #lease_keys ~= #KEYS - 2 then return {'CORRUPT'} end
for i = 3, #KEYS do
  if lease_keys[i - 2] ~= KEYS[i] then return {'UNKNOWN'} end
  if redis.call('TYPE', KEYS[i]).ok ~= 'zset' then return {'CORRUPT'} end
  local score = tonumber(redis.call('ZSCORE', KEYS[i], ARGV[1]) or '')
  if not score or score ~= expiry then return {'CORRUPT'} end
end
local extended = math.max(expiry, ms + lease_ms)
if extended >= 9007199254740991 then return {'INVALID'} end
redis.call('HSET', guard, 'last_ms', ms)
for i = 3, #KEYS do redis.call('ZADD', KEYS[i], extended, ARGV[1]) end
redis.call('HSET', decision, '__lease_expiry:' .. ARGV[1], tostring(extended))
return {'OK', tostring(extended)}
"""

CONSUME = r"""
local guard, decision = KEYS[1], KEYS[2]
if redis.call('TYPE', guard).ok ~= 'hash' or redis.call('TYPE', decision).ok ~= 'hash' then
  return {'CORRUPT'}
end
if redis.call('HGET', guard, 'epoch') ~= ARGV[1] or
   redis.call('HGET', decision, '__guard__') ~= ARGV[1] then return {'EPOCH'} end
if not server_generation(guard, false) then return {'SERVER_GENERATION'} end
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

# Each check runs on the very node executing the accounting script, avoiding a
# topology-query/admission race between separate client commands.
REGISTER_SCOPE = _SERVER_GENERATION + REGISTER_SCOPE
PUBLISH = _SERVER_GENERATION + PUBLISH
READ_POLICY = _SERVER_GENERATION + READ_POLICY
ADMIT = _SERVER_GENERATION + ADMIT
CONSUME = _SERVER_GENERATION + CONSUME
RENEW = _SERVER_GENERATION + RENEW
