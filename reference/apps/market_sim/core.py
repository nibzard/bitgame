"""market-sim — first meshkit app vertical.

A deterministic local-marketplace game that exercises every platform
primitive: broadcast listings, directed orders, escrow through a third
peer, vouch-weighted reputation, and cashu-token settlement. Pure
(state, event) -> (state, effects); no clock, no randomness.

Events (all bodies are canonical JSON):
  LIST    {listingId, item, qty, priceSats}          broadcast
  CANCEL  {listingId}                                 broadcast
  BUY     {orderId, listingId, qty}                   directed to seller
  HOLD    {orderId, token}                            escrow agent -> mesh
  RELEASE {orderId, outcome: "delivered"|"refunded"}  escrow agent -> mesh
  VOUCH   {target}                                    directed, reputation

Effects are AppEvents the host transmits per the manifest's permissions.
"""

from __future__ import annotations

import json
from typing import List, Tuple

# Statuses: open -> sold | cancelled; order: new -> held -> settled | refunded


def init_state(seed: bytes) -> dict:
    return {
        "seed": seed.hex(),
        "listings": {},   # listingId -> dict
        "orders": {},     # orderId -> dict
        "reputation": {}, # peerIdHex -> vouch count
        # SPEC 5.4: cross-sender causality is not guaranteed. An escrow HOLD
        # or RELEASE may apply before the BUY it depends on. Early events
        # park here and replay when the precondition lands.
        "pending": {},    # orderId -> [ {"type": "HOLD"|"RELEASE", ...event} ]
    }


_MAX_PENDING_PER_ORDER = 8


def reduce(state: dict, event) -> Tuple[dict, list]:
    handler = _HANDLERS.get(event.event_type)
    if handler is None:
        return state, []  # unknown verb: state unchanged, event still committed
    body = json.loads(event.body.decode()) if event.body else {}
    return handler(state, event, body)


def render_state(state: dict) -> dict:
    return {
        "listings": {
            lid: {"item": l["item"], "priceSats": l["priceSats"], "status": l["status"]}
            for lid, l in state["listings"].items()
        },
        "openOrders": sum(1 for o in state["orders"].values() if o["status"] != "settled"),
        "reputation": dict(state["reputation"]),
    }


# -- handlers -----------------------------------------------------------------


def _list(state: dict, event, body: dict) -> Tuple[dict, list]:
    lid = body.get("listingId")
    if not lid or lid in state["listings"]:
        return state, []
    if not isinstance(body.get("priceSats"), int) or body["priceSats"] <= 0:
        return state, []
    qty = body.get("qty", 1)
    state["listings"][lid] = {
        "seller": event.sender.hex(),
        "item": str(body.get("item", ""))[:40],
        "qty": qty,
        "priceSats": body["priceSats"],
        "seq": event.seq,
        "status": "open",
    }
    return state, []


def _cancel(state: dict, event, body: dict) -> Tuple[dict, list]:
    lid = body.get("listingId")
    listing = state["listings"].get(lid)
    if listing is None or listing["status"] != "open":
        return state, []
    if listing["seller"] != event.sender.hex():
        return state, []  # only the seller cancels
    listing["status"] = "cancelled"
    return state, []


def _buy(state: dict, event, body: dict) -> Tuple[dict, list]:
    oid = body.get("orderId")
    listing = state["listings"].get(body.get("listingId"))
    if not oid or oid in state["orders"] or listing is None:
        return state, []
    if listing["status"] != "open":
        return state, []
    qty = min(body.get("qty", 1), listing["qty"])
    if qty <= 0:
        return state, []
    listing["qty"] -= qty
    if listing["qty"] == 0:
        listing["status"] = "sold"
    state["orders"][oid] = {
        "buyer": event.sender.hex(),
        "listingId": body["listingId"],
        "item": listing["item"],
        "qty": qty,
        "priceSats": listing["priceSats"] * qty,
        "status": "new",
        "escrow": None,
    }
    _drain_pending(state, oid)
    return state, []


def _hold(state: dict, event, body: dict) -> Tuple[dict, list]:
    oid = body.get("orderId")
    order = state["orders"].get(oid)
    if order is None or order["status"] != "new":
        _park(state, oid, "HOLD", event, body)
        return state, []
    order["status"] = "held"
    order["escrow"] = event.sender.hex()
    order["token"] = str(body.get("token", ""))[:64]  # cashu token reference
    _drain_pending(state, oid)
    return state, []


def _release(state: dict, event, body: dict) -> Tuple[dict, list]:
    oid = body.get("orderId")
    order = state["orders"].get(oid)
    if order is None or order["status"] != "held":
        _park(state, oid, "RELEASE", event, body)
        return state, []
    if order["escrow"] != event.sender.hex():
        return state, []  # only the holding agent releases
    outcome = body.get("outcome")
    if outcome == "delivered":
        order["status"] = "settled"
        _bump(state, order["escrow"], +1)  # agent completed the flow
    elif outcome == "refunded":
        order["status"] = "refunded"
        _bump(state, order["escrow"], -1)  # agent failed the flow
    else:
        return state, []
    order.pop("token", None)
    return state, []


def _vouch(state: dict, event, body: dict) -> Tuple[dict, list]:
    target = body.get("target")
    if not isinstance(target, str) or len(target) > 16:
        return state, []
    if target == event.sender.hex():
        return state, []  # no self-vouching
    _bump(state, target, +1)
    return state, []


# -- out-of-order support ------------------------------------------------------


def _park(state: dict, oid, verb: str, event, body: dict) -> None:
    if not oid:
        return
    parked = state["pending"].setdefault(oid, [])
    if any(p["eventId"] == event.event_id.hex() for p in parked):
        return  # already parked by this exact event
    parked.append(
        {
            "type": verb,
            "eventId": event.event_id.hex(),
            "sender": event.sender.hex(),
            "token": str(body.get("token", ""))[:64],
            "outcome": body.get("outcome"),
        }
    )
    if len(parked) > _MAX_PENDING_PER_ORDER:
        del parked[0]  # bounded: drop oldest under junk floods


def _drain_pending(state: dict, oid: str) -> None:
    """Replay parked events for an order whose precondition just landed."""
    parked = state["pending"].get(oid)
    if not parked:
        return
    remaining = []
    for p in parked:
        order = state["orders"].get(oid)
        if order is None:
            remaining.append(p)
            continue
        if p["type"] == "HOLD" and order["status"] == "new":
            order["status"] = "held"
            order["escrow"] = p["sender"]
            order["token"] = p["token"]
        elif p["type"] == "RELEASE" and order["status"] == "held" and order["escrow"] == p["sender"]:
            if p["outcome"] == "delivered":
                order["status"] = "settled"
                _bump(state, order["escrow"], +1)
                order.pop("token", None)
            elif p["outcome"] == "refunded":
                order["status"] = "refunded"
                _bump(state, order["escrow"], -1)
                order.pop("token", None)
            else:
                remaining.append(p)
        else:
            remaining.append(p)
    if remaining:
        state["pending"][oid] = remaining
    else:
        state["pending"].pop(oid, None)


def _bump(state: dict, peer_hex: str, delta: int) -> None:
    """Vouches accumulate from 0; a failed escrow can drive an agent negative."""
    state["reputation"][peer_hex] = state["reputation"].get(peer_hex, 0) + delta


_HANDLERS = {
    "LIST": _list,
    "CANCEL": _cancel,
    "BUY": _buy,
    "HOLD": _hold,
    "RELEASE": _release,
    "VOUCH": _vouch,
}
