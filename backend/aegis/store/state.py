"""Hot-path state: kill flags, revocation, spend counters, rate windows.

Two implementations behind one interface:

  * MemoryStateStore — asyncio-lock guarded dicts. Zero infrastructure, and the
    single-process demo is exactly as atomic as Redis is.
  * RedisStateStore  — the same semantics as one Lua script, so the whole
    check-and-increment is a single atomic round trip even across many
    horizontally-scaled gateway replicas.

The critical operation is `try_commit`: it validates every spend and rate limit
*and* increments the counters in one indivisible step, so two concurrent
requests can never both slip past the same remaining budget.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..models import ReasonCode

DAY_TTL = 60 * 60 * 48
MIN_TTL = 120


def day_key(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).strftime("%Y%m%d")


def minute_bucket(ts: float | None = None) -> int:
    return int((ts or time.time()) // 60)


@dataclass
class Limits:
    per_txn_cap_cents: int
    daily_cap_cents: int
    fleet_cap_cents: int
    rate_limit_per_min: int
    payment_rate_per_min: int


@dataclass
class Counters:
    spend_today_cents: int = 0
    fleet_spend_today_cents: int = 0
    actions_this_min: int = 0
    payments_this_min: int = 0
    actions_today: int = 0
    blocked_today: int = 0
    last_action_at: float | None = None


@dataclass
class CommitOutcome:
    ok: bool
    reason_code: ReasonCode | None = None
    detail: str | None = None
    counters: Counters = field(default_factory=Counters)


def _evaluate(
    *,
    amount: int,
    is_payment: bool,
    limits: Limits,
    spend_today: int,
    fleet_spend_today: int,
    actions_this_min: int,
    payments_this_min: int,
) -> tuple[ReasonCode | None, str | None]:
    """Pure limit check, shared by the peek path and the memory commit path.

    Mirrored by COMMIT_LUA below; keep the two in step.
    """
    if amount > 0 and limits.per_txn_cap_cents and amount > limits.per_txn_cap_cents:
        return ReasonCode.OVER_TXN_CAP, f"${amount / 100:,.2f} over ${limits.per_txn_cap_cents / 100:,.2f} cap"
    if amount > 0 and limits.daily_cap_cents and spend_today + amount > limits.daily_cap_cents:
        remaining = max(limits.daily_cap_cents - spend_today, 0)
        return ReasonCode.OVER_DAILY_CAP, f"${remaining / 100:,.2f} of daily cap remaining"
    if amount > 0 and limits.fleet_cap_cents and fleet_spend_today + amount > limits.fleet_cap_cents:
        remaining = max(limits.fleet_cap_cents - fleet_spend_today, 0)
        return ReasonCode.OVER_FLEET_CAP, f"${remaining / 100:,.2f} of fleet cap remaining"
    if limits.rate_limit_per_min and actions_this_min + 1 > limits.rate_limit_per_min:
        return ReasonCode.RATE_LIMIT_EXCEEDED, f"{limits.rate_limit_per_min} actions/min exceeded"
    if is_payment and limits.payment_rate_per_min and payments_this_min + 1 > limits.payment_rate_per_min:
        return ReasonCode.PAYMENT_RATE_EXCEEDED, f"{limits.payment_rate_per_min} payments/min exceeded"
    return None, None


class StateStore(ABC):
    """Everything the decision path touches on the hot path."""

    # --- lifecycle ---------------------------------------------------------
    async def connect(self) -> None:  # pragma: no cover - trivial
        return None

    async def close(self) -> None:  # pragma: no cover - trivial
        return None

    # --- fleet kill switch -------------------------------------------------
    @abstractmethod
    async def get_halt(self) -> dict | None: ...

    @abstractmethod
    async def set_halt(self, by: str, reason: str) -> dict: ...

    @abstractmethod
    async def clear_halt(self) -> None: ...

    # --- per-agent status --------------------------------------------------
    @abstractmethod
    async def set_agent_status(self, agent_id: str, status: str) -> None: ...

    @abstractmethod
    async def get_agent_status(self, agent_id: str) -> str | None: ...

    @abstractmethod
    async def all_agent_status(self) -> dict[str, str]: ...

    # --- counters ----------------------------------------------------------
    @abstractmethod
    async def counters(self, agent_id: str) -> Counters: ...

    @abstractmethod
    async def try_commit(self, agent_id: str, amount: int, is_payment: bool, limits: Limits) -> CommitOutcome: ...

    async def try_reserve(
        self, agent_id: str, request_id: str, amount: int, is_payment: bool, limits: Limits
    ) -> CommitOutcome:
        """Atomically hold budget (reserve). Tracked by request_id for settle/release."""
        outcome = await self.try_commit(agent_id, amount, is_payment, limits)
        if outcome.ok:
            await self._track_reservation(agent_id, request_id, amount)
        return outcome

    @abstractmethod
    async def _track_reservation(self, agent_id: str, request_id: str, amount: int) -> None: ...

    @abstractmethod
    async def settle(self, agent_id: str, request_id: str, actual_cents: int) -> int:
        """Capture the actual cost and release any over-reservation. Returns cents released."""

    @abstractmethod
    async def release(self, agent_id: str, request_id: str) -> int:
        """Release a full reservation (e.g. downstream failure). Returns cents released."""

    @abstractmethod
    async def record_outcome(self, agent_id: str, allowed: bool) -> None: ...

    @abstractmethod
    async def fleet_spend_today(self) -> int: ...

    @abstractmethod
    async def reset(self) -> None: ...

    @abstractmethod
    async def reset_counters(self) -> None:
        """Clear today's spend/rate/activity counters, leaving kill state and
        revocations untouched. Lets a demo be re-run without a restart."""

    async def peek(self, agent_id: str, amount: int, is_payment: bool, limits: Limits) -> CommitOutcome:
        """Non-mutating limit check used by gate 3 so the deny reason is precise."""
        c = await self.counters(agent_id)
        code, detail = _evaluate(
            amount=amount,
            is_payment=is_payment,
            limits=limits,
            spend_today=c.spend_today_cents,
            fleet_spend_today=c.fleet_spend_today_cents,
            actions_this_min=c.actions_this_min,
            payments_this_min=c.payments_this_min,
        )
        return CommitOutcome(ok=code is None, reason_code=code, detail=detail, counters=c)


# ---------------------------------------------------------------------------
# In-process implementation
# ---------------------------------------------------------------------------
class MemoryStateStore(StateStore):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._halt: dict | None = None
        self._status: dict[str, str] = {}
        self._spend: dict[str, int] = {}
        self._fleet_spend: dict[str, int] = {}
        self._rate: dict[tuple[str, int], int] = {}
        self._pay_rate: dict[tuple[str, int], int] = {}
        self._actions_today: dict[str, int] = {}
        self._blocked_today: dict[str, int] = {}
        self._last_action: dict[str, float] = {}
        self._reservations: dict[str, tuple[str, int]] = {}

    async def get_halt(self) -> dict | None:
        return self._halt

    async def set_halt(self, by: str, reason: str) -> dict:
        self._halt = {"halted": True, "by": by, "reason": reason, "at": time.time()}
        return self._halt

    async def clear_halt(self) -> None:
        self._halt = None

    async def set_agent_status(self, agent_id: str, status: str) -> None:
        self._status[agent_id] = status

    async def get_agent_status(self, agent_id: str) -> str | None:
        return self._status.get(agent_id)

    async def all_agent_status(self) -> dict[str, str]:
        return dict(self._status)

    def _keys(self, agent_id: str) -> tuple[str, str, tuple[str, int], tuple[str, int]]:
        d, m = day_key(), minute_bucket()
        return f"{agent_id}:{d}", d, (agent_id, m), (f"pay:{agent_id}", m)

    async def counters(self, agent_id: str) -> Counters:
        sk, dk, rk, pk = self._keys(agent_id)
        return Counters(
            spend_today_cents=self._spend.get(sk, 0),
            fleet_spend_today_cents=self._fleet_spend.get(dk, 0),
            actions_this_min=self._rate.get(rk, 0),
            payments_this_min=self._pay_rate.get(pk, 0),
            actions_today=self._actions_today.get(sk, 0),
            blocked_today=self._blocked_today.get(sk, 0),
            last_action_at=self._last_action.get(agent_id),
        )

    async def try_commit(self, agent_id: str, amount: int, is_payment: bool, limits: Limits) -> CommitOutcome:
        sk, dk, rk, pk = self._keys(agent_id)
        async with self._lock:
            spend, fleet = self._spend.get(sk, 0), self._fleet_spend.get(dk, 0)
            acts, pays = self._rate.get(rk, 0), self._pay_rate.get(pk, 0)
            code, detail = _evaluate(
                amount=amount,
                is_payment=is_payment,
                limits=limits,
                spend_today=spend,
                fleet_spend_today=fleet,
                actions_this_min=acts,
                payments_this_min=pays,
            )
            if code is not None:
                return CommitOutcome(ok=False, reason_code=code, detail=detail)
            if amount:
                self._spend[sk] = spend + amount
                self._fleet_spend[dk] = fleet + amount
            self._rate[rk] = acts + 1
            if is_payment:
                self._pay_rate[pk] = pays + 1
            return CommitOutcome(ok=True, counters=await self.counters(agent_id))

    async def _track_reservation(self, agent_id: str, request_id: str, amount: int) -> None:
        if amount > 0:
            self._reservations[request_id] = (agent_id, amount)

    def _refund(self, agent_id: str, refund: int) -> None:
        if refund <= 0:
            return
        sk, dk, *_ = self._keys(agent_id)
        self._spend[sk] = max(0, self._spend.get(sk, 0) - refund)
        self._fleet_spend[dk] = max(0, self._fleet_spend.get(dk, 0) - refund)

    async def settle(self, agent_id: str, request_id: str, actual_cents: int) -> int:
        async with self._lock:
            reserved_entry = self._reservations.pop(request_id, None)
            if reserved_entry is None:
                return 0
            reserved_agent, reserved = reserved_entry
            if reserved_agent != agent_id:
                return 0
            refund = max(reserved - max(actual_cents, 0), 0)
            self._refund(agent_id, refund)
            return refund

    async def release(self, agent_id: str, request_id: str) -> int:
        async with self._lock:
            reserved_entry = self._reservations.pop(request_id, None)
            if reserved_entry is None:
                return 0
            reserved_agent, reserved = reserved_entry
            if reserved_agent != agent_id:
                return 0
            self._refund(agent_id, reserved)
            return reserved

    async def record_outcome(self, agent_id: str, allowed: bool) -> None:
        sk, *_ = self._keys(agent_id)
        self._actions_today[sk] = self._actions_today.get(sk, 0) + 1
        if not allowed:
            self._blocked_today[sk] = self._blocked_today.get(sk, 0) + 1
        self._last_action[agent_id] = time.time()

    async def fleet_spend_today(self) -> int:
        return self._fleet_spend.get(day_key(), 0)

    async def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    async def reset_counters(self) -> None:
        async with self._lock:
            self._spend.clear()
            self._fleet_spend.clear()
            self._rate.clear()
            self._pay_rate.clear()
            self._actions_today.clear()
            self._blocked_today.clear()
            self._last_action.clear()
            self._reservations.clear()


# ---------------------------------------------------------------------------
# Redis implementation
# ---------------------------------------------------------------------------
#: KEYS[1] agent spend, KEYS[2] fleet spend, KEYS[3] action window, KEYS[4] payment window
COMMIT_LUA = """
local amount     = tonumber(ARGV[1])
local txn_cap    = tonumber(ARGV[2])
local daily_cap  = tonumber(ARGV[3])
local fleet_cap  = tonumber(ARGV[4])
local rate_cap   = tonumber(ARGV[5])
local pay_cap    = tonumber(ARGV[6])
local is_payment = tonumber(ARGV[7])
local day_ttl    = tonumber(ARGV[8])
local min_ttl    = tonumber(ARGV[9])

local spend = tonumber(redis.call('GET', KEYS[1]) or '0')
local fleet = tonumber(redis.call('GET', KEYS[2]) or '0')
local acts  = tonumber(redis.call('GET', KEYS[3]) or '0')
local pays  = tonumber(redis.call('GET', KEYS[4]) or '0')

if amount > 0 and txn_cap > 0 and amount > txn_cap then
  return {'over_txn_cap', tostring(txn_cap)}
end
if amount > 0 and daily_cap > 0 and spend + amount > daily_cap then
  return {'over_daily_cap', tostring(daily_cap - spend)}
end
if amount > 0 and fleet_cap > 0 and fleet + amount > fleet_cap then
  return {'over_fleet_cap', tostring(fleet_cap - fleet)}
end
if rate_cap > 0 and acts + 1 > rate_cap then
  return {'rate_limit_exceeded', tostring(rate_cap)}
end
if is_payment == 1 and pay_cap > 0 and pays + 1 > pay_cap then
  return {'payment_rate_exceeded', tostring(pay_cap)}
end

if amount > 0 then
  redis.call('INCRBY', KEYS[1], amount); redis.call('EXPIRE', KEYS[1], day_ttl)
  redis.call('INCRBY', KEYS[2], amount); redis.call('EXPIRE', KEYS[2], day_ttl)
end
redis.call('INCR', KEYS[3]); redis.call('EXPIRE', KEYS[3], min_ttl)
if is_payment == 1 then
  redis.call('INCR', KEYS[4]); redis.call('EXPIRE', KEYS[4], min_ttl)
end
return {'ok', ''}
"""

REFUND_LUA = """
local refund = tonumber(ARGV[1])
if refund > 0 then
  redis.call('DECRBY', KEYS[1], refund)
  redis.call('DECRBY', KEYS[2], refund)
end
return refund
"""


class RedisStateStore(StateStore):
    def __init__(self, url: str) -> None:
        self.url = url
        self._redis = None
        self._commit = None

    async def connect(self) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self.url, decode_responses=True)
        await self._redis.ping()
        self._commit = self._redis.register_script(COMMIT_LUA)
        self._refund = self._redis.register_script(REFUND_LUA)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    # --- keys --------------------------------------------------------------
    @staticmethod
    def _k(agent_id: str) -> tuple[str, str, str, str]:
        d, m = day_key(), minute_bucket()
        return (
            f"aegis:spend:{agent_id}:{d}",
            f"aegis:spend:_fleet:{d}",
            f"aegis:rate:{agent_id}:{m}",
            f"aegis:payrate:{agent_id}:{m}",
        )

    async def get_halt(self) -> dict | None:
        raw = await self._redis.hgetall("aegis:fleet:halt")
        if not raw:
            return None
        return {"halted": True, "by": raw.get("by"), "reason": raw.get("reason"), "at": float(raw.get("at", 0))}

    async def set_halt(self, by: str, reason: str) -> dict:
        payload = {"by": by, "reason": reason, "at": str(time.time())}
        await self._redis.hset("aegis:fleet:halt", mapping=payload)
        return {"halted": True, **payload, "at": float(payload["at"])}

    async def clear_halt(self) -> None:
        await self._redis.delete("aegis:fleet:halt")

    async def set_agent_status(self, agent_id: str, status: str) -> None:
        await self._redis.hset("aegis:agent:status", agent_id, status)

    async def get_agent_status(self, agent_id: str) -> str | None:
        return await self._redis.hget("aegis:agent:status", agent_id)

    async def all_agent_status(self) -> dict[str, str]:
        return await self._redis.hgetall("aegis:agent:status") or {}

    async def counters(self, agent_id: str) -> Counters:
        sk, fk, rk, pk = self._k(agent_id)
        d = day_key()
        vals = await self._redis.mget(
            sk, fk, rk, pk, f"aegis:actions:{agent_id}:{d}", f"aegis:blocked:{agent_id}:{d}", f"aegis:last:{agent_id}"
        )
        return Counters(
            spend_today_cents=int(vals[0] or 0),
            fleet_spend_today_cents=int(vals[1] or 0),
            actions_this_min=int(vals[2] or 0),
            payments_this_min=int(vals[3] or 0),
            actions_today=int(vals[4] or 0),
            blocked_today=int(vals[5] or 0),
            last_action_at=float(vals[6]) if vals[6] else None,
        )

    async def try_commit(self, agent_id: str, amount: int, is_payment: bool, limits: Limits) -> CommitOutcome:
        keys = list(self._k(agent_id))
        res = await self._commit(
            keys=keys,
            args=[
                amount,
                limits.per_txn_cap_cents,
                limits.daily_cap_cents,
                limits.fleet_cap_cents,
                limits.rate_limit_per_min,
                limits.payment_rate_per_min,
                1 if is_payment else 0,
                DAY_TTL,
                MIN_TTL,
            ],
        )
        code = res[0] if isinstance(res, (list, tuple)) else res
        if code == "ok":
            return CommitOutcome(ok=True, counters=await self.counters(agent_id))
        return CommitOutcome(ok=False, reason_code=ReasonCode(code), detail=None)

    async def _track_reservation(self, agent_id: str, request_id: str, amount: int) -> None:
        if amount > 0:
            await self._redis.set(f"aegis:resv:{request_id}", f"{agent_id}:{amount}", ex=DAY_TTL)

    async def _reserved_amount(self, agent_id: str, request_id: str) -> int:
        raw = await self._redis.getdel(f"aegis:resv:{request_id}")
        if not raw:
            return 0
        owner, amount = raw.split(":", 1)
        if owner != agent_id:
            return 0
        return int(amount)

    async def settle(self, agent_id: str, request_id: str, actual_cents: int) -> int:
        reserved = await self._reserved_amount(agent_id, request_id)
        refund = max(reserved - max(actual_cents, 0), 0)
        if refund:
            sk, fk, *_ = self._k(agent_id)
            await self._refund(keys=[sk, fk], args=[refund])
        return refund

    async def release(self, agent_id: str, request_id: str) -> int:
        reserved = await self._reserved_amount(agent_id, request_id)
        if reserved:
            sk, fk, *_ = self._k(agent_id)
            await self._refund(keys=[sk, fk], args=[reserved])
        return reserved

    async def record_outcome(self, agent_id: str, allowed: bool) -> None:
        d = day_key()
        pipe = self._redis.pipeline()
        pipe.incr(f"aegis:actions:{agent_id}:{d}")
        pipe.expire(f"aegis:actions:{agent_id}:{d}", DAY_TTL)
        if not allowed:
            pipe.incr(f"aegis:blocked:{agent_id}:{d}")
            pipe.expire(f"aegis:blocked:{agent_id}:{d}", DAY_TTL)
        pipe.set(f"aegis:last:{agent_id}", time.time())
        await pipe.execute()

    async def fleet_spend_today(self) -> int:
        return int(await self._redis.get(f"aegis:spend:_fleet:{day_key()}") or 0)

    async def reset(self) -> None:
        keys = [k async for k in self._redis.scan_iter("aegis:*")]
        if keys:
            await self._redis.delete(*keys)

    async def reset_counters(self) -> None:
        for pattern in ("aegis:spend:*", "aegis:rate:*", "aegis:payrate:*",
                        "aegis:actions:*", "aegis:blocked:*", "aegis:last:*"):
            keys = [k async for k in self._redis.scan_iter(pattern)]
            if keys:
                await self._redis.delete(*keys)


async def open_state_store(redis_url: str | None, *, fail_closed: bool = False) -> StateStore:
    """Prefer Redis when configured. Fail closed if required but unreachable."""
    if redis_url:
        store = RedisStateStore(redis_url)
        try:
            await store.connect()
            return store
        except Exception as exc:
            if fail_closed:
                raise RuntimeError(f"Redis required but unavailable: {exc}") from exc
            print(f"[aegis] Redis unavailable ({exc}); using in-process state store")
    store = MemoryStateStore()
    await store.connect()
    return store
