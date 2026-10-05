# Changelog

## 0.1.0 — 2026-10-05

First public alpha. Tested with The Witcher 3 5.00c (Steam), Minecraft Java 1.21.11, Fabric Loader 0.19.5,
Fabric API 0.141.6+1.21.11.

- Minecraft blocks in the Witcher world from a headless Fabric server: place, break, craft by Minecraft rules;
  blocks rendered by the Witcher with its lighting and collision.
- Steve mode (F4): a hidden Minecraft client plays Geralt; first-person camera, Minecraft hand/item/HUD overlay,
  Minecraft movement physics on Witcher terrain and house collision.
- Combat: Minecraft melee and arrows against Witcher NPCs with vanilla parry and dodge; NPC hits on the Minecraft
  player with Minecraft shield and armour; Minecraft arrows shown as Witcher arrows, sticking in walls.
- Minecraft horse with riding; Steve-shaped body on Geralt's bones (REDkit build).
- `mcw.ps1` / `mcw.cmd`: requirements check, one-command install (REDkit build or lite mode), start, stop, status,
  uninstall; automatic detection of the game (Steam, GOG), REDkit, the Minecraft jar and a JDK 21.
