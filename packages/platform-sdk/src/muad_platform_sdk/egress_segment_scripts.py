"""Atomic execution-segment gates; all supplied keys share the meter hash tag."""
from .shared_redis_scripts import _SERVER_GENERATION, ADMIT, CONSUME, RENEW

SEGMENT_CONTROL = _SERVER_GENERATION + r"""
local guard, scope, index, segment = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
if redis.call('TYPE', guard).ok ~= 'hash' or redis.call('TYPE', scope).ok ~= 'hash' or
   redis.call('TYPE', index).ok ~= 'hash' then return {'CORRUPT'} end
if redis.call('HGET', guard, 'epoch') ~= ARGV[1] or
   redis.call('HGET', index, '__guard__') ~= ARGV[1] then return {'EPOCH'} end
local generation_ok = server_generation(guard, false)
if not generation_ok then return {'SERVER_GENERATION'} end
if redis.call('HGET', scope, 'identity') ~= ARGV[2] or
   redis.call('HGET', index, ARGV[3]) ~= ARGV[2] then return {'MISSING_SCOPE'} end
local fence, lease, margin = tonumber(ARGV[4]), tonumber(ARGV[6]), tonumber(ARGV[7])
if not fence or fence <= 0 or fence >= 9007199254740991 or
   fence ~= math.floor(fence) or ARGV[4] ~= string.format('%.0f', fence) or
   ARGV[5] == '' or (ARGV[8] ~= 'activate' and ARGV[8] ~= 'stopped') or
   not lease or not margin or lease ~= math.floor(lease) or margin ~= math.floor(margin) or
   lease < 0 or margin < 0 then return {'INVALID'} end
if ARGV[8] == 'activate' and (lease <= 0 or margin <= 0) then return {'INVALID'} end
local now = redis.call('TIME')
local ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local last = tonumber(redis.call('HGET', guard, 'last_ms') or '')
if not last then return {'CORRUPT'} end
if ms < last then return {'CLOCK'} end
local until_ms, safe_after = ms + lease, ms + lease + margin
if safe_after >= 9007199254740991 then return {'INVALID'} end
local marker = redis.call('HGET', index, 'segment:' .. ARGV[3])
local kind = redis.call('TYPE', segment).ok
local scope_marker = redis.call('HGET', scope, 'segment_key')
if marker or scope_marker then
  if scope_marker ~= segment or marker ~= segment then return {'MISSING_SEGMENT'} end
  if marker ~= segment or kind ~= 'hash' then return {'MISSING_SEGMENT'} end
  if redis.call('HGET', segment, 'epoch') ~= ARGV[1] or
     redis.call('HGET', segment, 'identity') ~= ARGV[2] then return {'CORRUPT'} end
  local old_raw = redis.call('HGET', segment, 'fence') or ''
  local old_fence = tonumber(old_raw)
  local old_until = tonumber(redis.call('HGET', segment, 'until_ms') or '')
  local old_safe = tonumber(redis.call('HGET', segment, 'safe_after') or '')
  if not old_fence or old_fence <= 0 or old_raw ~= string.format('%.0f', old_fence) or
     not old_until or not old_safe or old_safe < old_until or
     old_until < 0 or old_until ~= math.floor(old_until) or
     old_safe ~= math.floor(old_safe) or old_safe >= 9007199254740991 then return {'CORRUPT'} end
  if fence < old_fence then return {'FENCE'} end
  if fence == old_fence then
    if redis.call('HGET', segment, 'segment_id') ~= ARGV[5] then return {'FENCE'} end
    if ARGV[8] == 'activate' then
      if ms >= old_until then return {'EXPIRED'} end
      until_ms = math.max(old_until, until_ms)
      safe_after = math.max(old_safe, until_ms + margin)
    end
  elseif ARGV[8] == 'stopped' then return {'FENCE'}
  elseif ms < old_safe then return {'BUSY'} end
else
  if kind ~= 'none' or ARGV[8] ~= 'activate' then return {'MISSING_SEGMENT'} end
end
if ARGV[8] == 'stopped' then until_ms, safe_after = 0, ms end
if safe_after >= 9007199254740991 then return {'INVALID'} end
-- All state/argument checks precede the first mutation: Lua has no rollback.
redis.call('HSET', guard, 'last_ms', ms)
redis.call('HSET', index, 'segment:' .. ARGV[3], segment)
redis.call('HSET', scope, 'segment_key', segment)
redis.call('HSET', segment, 'epoch', ARGV[1], 'identity', ARGV[2], 'fence', ARGV[4],
           'segment_id', ARGV[5], 'until_ms', string.format('%.0f', until_ms),
           'safe_after', string.format('%.0f', safe_after))
return {'OK'}
"""

_SEGMENT_GATE = r"""
local guard, segment, index, scope = KEYS[#KEYS - 3], KEYS[#KEYS - 2], KEYS[#KEYS - 1], KEYS[#KEYS]
local epoch, fence = ARGV[#ARGV - 4], ARGV[#ARGV - 3]
local segment_id, identity, scope_name = ARGV[#ARGV - 2], ARGV[#ARGV - 1], ARGV[#ARGV]
if redis.call('TYPE', guard).ok ~= 'hash' or redis.call('TYPE', segment).ok ~= 'hash' or
   redis.call('TYPE', index).ok ~= 'hash' or redis.call('TYPE', scope).ok ~= 'hash' then
  return {'MISSING_SEGMENT'}
end
if redis.call('HGET', guard, 'epoch') ~= epoch or
   redis.call('HGET', segment, 'epoch') ~= epoch then return {'EPOCH'} end
if redis.call('HGET', scope, 'segment_key') ~= segment or
   redis.call('HGET', index, 'segment:' .. scope_name) ~= segment then return {'MISSING_SEGMENT'} end
if redis.call('HGET', segment, 'identity') ~= identity or
   redis.call('HGET', segment, 'fence') ~= fence or
   redis.call('HGET', segment, 'segment_id') ~= segment_id then return {'FENCE'} end
local now = redis.call('TIME')
local ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local last = tonumber(redis.call('HGET', guard, 'last_ms') or '')
local expires = tonumber(redis.call('HGET', segment, 'until_ms') or '')
if not last or not expires or expires ~= math.floor(expires) or
   expires >= 9007199254740991 then return {'CORRUPT'} end
if ms < last then return {'CLOCK'} end
if ms >= expires then return {'EXPIRED_SEGMENT'} end
local original_keys, original_args = {}, {}
for i = 1, #KEYS - 4 do original_keys[i] = KEYS[i] end
for i = 1, #ARGV - 5 do original_args[i] = ARGV[i] end
local decision, attempt = original_keys[decision_position], original_args[attempt_position]
if redis.call('TYPE', decision).ok ~= 'hash' then return {'CORRUPT'} end
local owner = cjson.encode({segment, fence, segment_id})
local recorded_owner = redis.call('HGET', decision, '__segment_owner:' .. attempt)
if operation_kind ~= 'admit' or redis.call('HEXISTS', decision, attempt) == 1 then
  if recorded_owner ~= owner then return {'FENCE'} end
elseif recorded_owner and recorded_owner ~= owner then return {'FENCE'} end
local result = operation(original_keys, original_args)
if operation_kind == 'admit' and (result[1] == 'OK' or result[1] == 'DUPLICATE') then
  redis.call('HSET', decision, '__segment_owner:' .. attempt, owner)
end
return result
"""

# The original operation gets its own local KEYS/ARGV parameters. The segment
# check and all accounting run atomically in one EVAL, without changing its key
# offsets or omitting the extra guard/segment keys from Redis Cluster routing.
def _fenced(script: str, kind: str, decision_position: int, attempt_position: int) -> str:
    metadata = (f'local operation_kind = "{kind}"\n'
                f'local decision_position, attempt_position = {decision_position}, {attempt_position}\n')
    return metadata + 'local function operation(KEYS, ARGV)\n' + script + '\nend\n' + _SEGMENT_GATE


FENCED_OPERATIONS = {
    ADMIT: _fenced(ADMIT, "admit", 3, 3),
    CONSUME: _fenced(CONSUME, "consume", 2, 2),
    RENEW: _fenced(RENEW, "renew", 1, 1),
}
