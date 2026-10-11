# Marketplace: deferred exploration

The marketplace is outside the current product scope. The first product
is one cooperative game for two or three nearby people.

## Why it is deferred

A marketplace needs discovery, inventory ownership, competing-order rules,
payment, and dispute handling. Intermittent connections make shared state
harder to maintain. Escrow introduces a trusted person and an operational
role that a game does not require.

A working transport does not establish these rules. The game should first
show that nearby groups can join, recover from disconnection, and finish
something they value.

## Existing simulation

`reference/apps/market_sim/` remains an earlier experiment. It demonstrates
listings, purchases, holds, releases, and reputation counters in a scripted
scenario. It does not implement payment verification or agreed escrow
appointment. Different arrival orders can produce different final states.

Do not use this simulation to manage real trades or infer settlement guarantees.
The passing tests cover the scripted behavior, not a complete marketplace.

## Conditions for revisiting

Revisit this direction only after identifying a specific local trading need.
Start with discovery and direct communication. Treat inventory, payment,
and disputes as separate product decisions with explicit authority rules.

A game library or transport adapter may help later. Neither requires an
app marketplace, reputation system, or payment layer now.
