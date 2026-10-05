# Minecraft × The Witcher 3

[Русская версия](README.ru.md)

Real Minecraft running *inside* The Witcher 3: Wild Hunt. Place and break Minecraft blocks in White Orchard, craft by
Minecraft recipes, fight Witcher NPCs with a Minecraft sword, bow and shield, ride a Minecraft horse. Press F4 and Geralt becomes Steve: the Minecraft hand, held item and HUD are drawn over the Witcher
picture, movement uses Minecraft physics on the Witcher world, and the Witcher renders everything with its own
lighting, shadows and collision.

Both games run at the same time. A headless Minecraft server owns the rules (blocks, inventory, crafting, mobs,
damage), a hidden Minecraft client plays Geralt, and a small Python bridge mirrors it all into the Witcher through
the game's own script debugging channel. There is no native code injected into The Witcher 3: it is a WitcherScript
mod plus the engine's debug protocol. The idea comes from [SkyCraft](https://github.com/chasmlol/SkyCraft)
(Minecraft in Skyrim).

> **Status: experimental alpha (0.1.0).** Developed and tested on one PC: Windows 11, The Witcher 3 5.00c on Steam,
> Minecraft 1.21.11. Expect rough edges; see [Known issues](#known-issues).

## Features

- Minecraft blocks in the Witcher world: Minecraft decides what can be placed, mined and crafted (a crafting table
  must be nearby, mining time follows Minecraft rules); the Witcher shows the blocks with its own lighting and
  collision, so Geralt stands on them.
- **Steve mode (F4):** first person, Minecraft hand/item/HUD overlaid on the Witcher, WASD/jump/sneak/sprint with
  Minecraft physics on the Witcher terrain, walls and furniture (houses can be entered).
- **Combat:** Minecraft sword and bow against Witcher NPCs (the hit is Geralt's own sword hit scaled by the Minecraft
  damage; NPCs parry and dodge the Witcher way), NPC hits land on the Minecraft player (Minecraft shield and armour
  work). Minecraft arrows fly as Witcher arrows and stick in walls.
- **Minecraft horse:** X calls it, right-click to mount; it rides with Minecraft physics over the Witcher world.
- Outside first person Geralt wears a Steve-shaped body made of Minecraft boxes on his bones (with a skin of your
  choice), so it follows his animations.
- Starter kit, crafting menu; Minecraft explosions (creepers) show up as Witcher barrel blasts.

## Requirements

| What | Version | Notes |
|---|---|---|
| Windows | 10 or 11, x64 | |
| The Witcher 3: Wild Hunt | **5.00c** (next-gen patch 5.0, exe 5.0.0.1044392) | Steam (tested) or GOG (paths supported, untested). Other 5.x builds may work, 4.x will not |
| Minecraft: Java Edition | **1.21.11** | You need to own it. Start 1.21.11 once in the official launcher: its jar is used to build the block textures and models |
| Java | JDK **21** | Found automatically in the Minecraft launcher's runtime; otherwise install [Temurin 21](https://adoptium.net) |
| Python | **3.11+** (tested on 3.12) | [python.org](https://www.python.org/downloads/), tick "Add python.exe to PATH" |
| The Witcher 3 REDkit | optional | Free for Witcher 3 owners on Steam/GOG, ~110 GB. Builds the Minecraft blocks and the Steve/horse models. Without it: [lite mode](#lite-mode) |
| Visual Studio 2022 with C++ | optional | Only to build the overlay yourself; otherwise take `mcw_overlay.exe` from the GitHub release |

Downloaded automatically during install: Fabric Loader 0.19.5, Fabric API 0.141.6+1.21.11, Gradle 9.7.1, the
Minecraft server and client libraries. RAM: the Minecraft server and client need about 4 GB on top of the Witcher.

## Install

1. Get the repository (`git clone` or "Code → Download ZIP" and unpack anywhere).
2. Close The Witcher 3, then run in the repository folder:

   ```bat
   mcw install
   ```

   (from PowerShell: `.\mcw.cmd install`). It checks the requirements and then:
   - backs up `Documents\The Witcher 3` settings and saves to `%LOCALAPPDATA%\mcw\backups` (once);
   - asks you to accept the [Minecraft EULA](https://aka.ms/MinecraftEULA) for the local server;
   - downloads the Fabric server launcher and builds the two Minecraft mods (the first build downloads Minecraft,
     a few minutes);
   - builds the overlay if Visual Studio is installed;
   - installs the Witcher script mod into `<game>\mods\modMCW`;
   - with REDkit: builds the blocks and the Steve/horse models from **your** Minecraft jar (~10 minutes) and installs
     them into `<game>\mods\modMCWBlocks` and `modMCWModels`.

   Options: `-Lite` (skip REDkit even if installed), `-Skin <png>` (your 64x64 skin with classic arms for the Steve
   body; see [assets_local/skins](assets_local/skins/README.md)), `-AcceptEula`, `-NoBackup`.
3. `mcw doctor` shows what is found and what is missing.

Paths are detected automatically (Steam libraries, GOG, Minecraft launcher). Override them with the environment
variables `MCW_W3_DIR`, `MCW_REDKIT_DIR`, `MCW_MC_JAR`, `MCW_JAVA_HOME`, `MCW_PYTHON`.

## Play

1. Double-click **`mcw.cmd`** (same as `mcw start`). It launches The Witcher 3 through Steam with the debug channel
   (`-net -debugscripts`; the first start after an install also recompiles the scripts), the Minecraft server, the
   hidden Minecraft client, the bridge and the overlay. If Steam asks whether to allow the launch options, allow.
2. Load a save (everything was tested in White Orchard). Press **F4** for Steve mode.
3. **Keep the Witcher window focused.** The engine pauses the game when it loses focus; the Minecraft world freezes
   with it.
4. To stop: `mcw stop` (the Minecraft world is saved). Save and quit the Witcher from its menu, or use
   `mcw stop -CloseGame`.

`mcw status` shows what is running. Bridge log: `bridge\logs\bridge.log`.

### Controls (with the Witcher window focused)

| Key | Action |
|---|---|
| F4 | Steve mode on/off |
| *Steve mode:* W A S D, Space, Left Shift, Left Ctrl | Walk, jump, sneak, sprint (Minecraft physics) |
| *Steve mode:* left / right / middle mouse | Attack or mine / place or use / pick block. Right mouse with a bow: draw, release to shoot; with a shield: block |
| *Steve mode:* 1–9, mouse wheel | Hotbar slot |
| *Steve mode:* E | Witcher interaction if the Witcher shows a prompt (doors, NPCs, chests, loot), otherwise the Minecraft inventory |
| *Steve mode:* Q | Drop the held item |
| *Steve mode:* X | Call the Minecraft horse. Right mouse on it: mount; Left Shift: dismount (outside Steve mode X is Roach) |
| F2 | Building mode without Steve mode: 9 / mouse 5 place, 8 / mouse 4 break, NumPad 1–9 slot |
| F3, PageUp/PageDown, 0 | Crafting list, scroll, craft (a crafting table must be nearby) |
| F6 | Test: spawn a creeper 6 m away |

Dialogues, cutscenes and Roach rides hand control back to Geralt automatically and return to Steve mode after.
Geralt gets a starter kit on first join (iron sword, bow, tools, blocks, food, shield, iron armour, arrows); the
inventory is kept on death. The hidden client is muted.

## Lite mode

Without REDkit (or with `mcw install -Lite`) everything works, but Minecraft blocks are shown as Witcher crates and
Geralt keeps his own body in cutscenes; the Minecraft horse has no model. Install REDkit and run `mcw install` again
to get the full version.

## Uninstall

`mcw uninstall` stops everything and removes `modMCW`, `modMCWBlocks` and `modMCWModels` from the game folder.
`-Purge` also deletes the build caches in `%LOCALAPPDATA%\mcw` (backups are kept). To restore your Witcher settings
from the backup, see the "Откат" (rollback) section of [spike/launch.md](spike/launch.md). Then delete the
repository folder.

## Security: the debug port

`-net` makes The Witcher 3 listen for its script debugger on **TCP port 37001 on all network interfaces** (checked on
5.00c). While the game runs this way, anyone on your local network could run script commands in it. The first time,
Windows Firewall asks about `witcher3.exe`: do not allow it on public networks. To block the port for other machines
(the bridge uses `127.0.0.1` and keeps working), run once in an administrator PowerShell:

```powershell
New-NetFirewallRule -DisplayName "MCW - block W3 debug port" -Direction Inbound -Protocol TCP -LocalPort 37000,37001 `
  -Program "<game>\bin\x64_dx12\witcher3.exe" -Action Block
```

The Minecraft server listens on `127.0.0.1` only and runs in offline mode. Launch the Witcher without `mcw` (plain
Steam "Play") when you are not using the mod.

## Troubleshooting

| Problem | What to do |
|---|---|
| `mcw doctor` shows FAIL | Install what the line says; set the `MCW_*` variable if it is installed in an unusual place |
| "There were errors compiling scripts" when the game starts | Answer "No", run `mcw doctor`. Another script mod may conflict with `modMCW`; try without it |
| No Minecraft hand/HUD over the Witcher | `overlay\mcw_overlay.exe` is missing (see Requirements) or the client is still starting (15–40 s) |
| Blocks are crates | Lite mode: REDkit not installed or `-Lite` |
| Nothing happens in game | `mcw status`; the bridge log `bridge\logs\bridge.log` (UTF-8: `Get-Content bridge\logs\bridge.log -Tail 30 -Encoding UTF8`) |
| Install step failed | Its log is in `%LOCALAPPDATA%\mcw\logs` |

Other logs: `mc\run\logs\latest.log` (server), `mcclient\run\logs\latest.log` (client),
`Documents\The Witcher 3\scriptslog.txt` (Witcher scripts).

## Known issues

- Alpha quality, tested on one machine. GOG installs are detected but untested.
- Only 10 block types have real textures (grass, dirt, stone, cobblestone, oak log, planks, sand, crafting table,
  oak leaves, glass); other blocks are grey cubes.
- Combat was play-tested against geese and chickens only. Not play-tested yet: fights with wolves and bandits
  (damage balance), NPC parry/dodge, the Minecraft shield, arrows sticking in walls, the latest horse riding fixes,
  the Steve body in an actual cutscene.
- Many White Orchard houses are solid in the Witcher itself; thin door leaves can be walked through.
- The Witcher pauses when it loses focus (engine behaviour, cannot be changed by scripts).
- Saves made with the mod are flagged as modded by the game (as with any script mod).

## How it works

```
Minecraft 1.21.11 server (Fabric, headless) ── JSON lines, 127.0.0.1:25600 ──┐
Hidden Minecraft client (Fabric mod mcwc) ──── JSON lines, 127.0.0.1:25601 ──┤
                                                                             bridge (Python) ── debug exec, 127.0.0.1:37001 ── The Witcher 3 (modMCW scripts)
Overlay (Windows.Graphics.Capture, colour key) ── draws the client's hand and HUD over the Witcher window
```

| Folder | What |
|---|---|
| `mcw.ps1`, `mcw.cmd` | Installer and launcher |
| `spike/` | Witcher 3 script mod (`mod/modMCW`), debug-channel client and tools |
| `mc/` | Fabric server mod (Java 21) |
| `mcclient/` | Hidden Fabric client mod (Java 21) |
| `bridge/` | The bridge (Python, standard library only) |
| `overlay/` | GPU overlay (C++) |
| `tools/` | Witcher file tools and the REDkit build of blocks and models |
| `docs/` | Developer documentation (in Russian): [DEVELOPMENT](docs/DEVELOPMENT.md), [NOTES](docs/NOTES.md) (verified engine facts), [PLAN](docs/PLAN.md), [REDKIT](docs/REDKIT.md), [STEVE](docs/STEVE.md) |

Tests: `python -m pytest -q` in `spike/`, `bridge/`, `tools/` and `mc/` (`test_fake_bridge.py`); `ruff check .`;
`python spike/ws_lint.py spike/mod/modMCW/content/scripts/local/*.ws` (needs the game for the vanilla script index).
Dev dependencies: `pip install -r requirements-dev.txt`.

## Credits

- [SkyCraft](https://github.com/chasmlol/SkyCraft) by chasmlol — the original "real Minecraft inside another game"
  idea and the overall scheme.
- [Fabric](https://fabricmc.net/) — Loader, API and Loom.
- Skin authors are credited in [assets_local/skins](assets_local/skins/README.md) (skins are not redistributed).
- Built with AI: the code and documentation were written with [Claude Code](https://claude.com/claude-code)
  (Anthropic's Claude) under the direction and testing of the repository author.

## Legal

NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT. Not affiliated with or
endorsed by CD PROJEKT RED. Minecraft is a trademark of Mojang; The Witcher is a trademark of CD PROJEKT S.A.
This repository contains no game files: block textures and models are built on your machine from your own copies
of the games. You need legitimate copies of both games.

## License

[MIT](LICENSE) for the code in this repository.
