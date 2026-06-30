# context #1

## 1. Game Loop State
- Framework: Pygame
- Entry point: `app.py`
- Main window: `1280x720` in windowed mode, or fullscreen if `settings.fullscreen` is enabled
- Frame cap: `60 FPS` via `clock.tick(60)`
- Current engine setup:
  - `StateManager` drives the app
  - `InputRouter` converts raw input into state actions
  - `GameState` owns the legacy `Game` instance for gameplay
  - `PauseState` is pushed as an overlay when the player pauses

## 2. Core State Machine
- Active states in the codebase:
  - `MenuState`
  - `GameState`
  - `PauseState`
  - `LevelsState`
  - `SkinsState`
  - `OptionsState`
- Global runtime state:
  - `settings.selected_level` tracks the current selected map
  - `settings.playable_levels` stores level unlock flags
  - `settings.selected_skin` and `settings.selected_weapon` track loadout
  - `settings.fullscreen`, `settings.music_volume`, `settings.sound_volume`
  - `Game.level`, `Game.timer`, `Game.screenshake`, `Game.running`
  - `Game.players`, `Game.enemies`, `Game.projectiles`, `Game.clouds`, `Game.tilemap`

## 3. Assets & Paths
- Core image and animation paths currently mapped in `game.py`:
  - `data/images/background-big.png`
  - `data/images/entities/player.png`
  - `data/images/gun.png`
  - `data/images/projectile.png`
  - `data/images/clouds/*`
  - `data/images/tiles/decor/*`
  - `data/images/tiles/grass/*`
  - `data/images/tiles/large_decor/*`
  - `data/images/tiles/stone/*`
  - `data/images/tiles/collectables/coin/*`
  - `data/images/collectables/apple/*`
  - `data/images/tiles/collectables/flag/*`
- Player skin animation folders discovered in the repo:
  - `data/images/entities/player/default/*`
  - `data/images/entities/player/red/*`
  - `data/images/entities/player/golden/*`
  - `data/images/entities/player/archer/*`
- Map files currently present:
  - `data/maps/0.json` through `data/maps/15.json`
- Audio is handled through `AudioService`; gameplay sound keys currently used include:
  - `jump`
  - `shoot`
  - `hit`

## 4. Completed Features
- Player movement, jumping, wall sliding, and gravity are functioning in the current build
- Cloud sprites now behave like jump-through platforms, so the player can land on them from above
- Enemy patrol logic was updated so enemies walk until an edge or wall, then turn around
- Projectile firing is wired for player input and enemy AI
- Golden skin exists and has stronger movement tuning than the default skins
- Archer skin assets are now discovered and selectable
- Progress now starts locked except for level 0
- The state manager flow is active, with menu, levels, skins, options, gameplay, and pause states in place

## 5. Current Bug / Task
- Current file: `scripts/progress_tracker.py`
- Current class: `ProgressTracker`
- Current method being tuned: `__post_init__`
- Current behavior being enforced:
  - only the first level starts unlocked
  - all later levels begin locked
- Recent related logic also touches:
  - `scripts/state_manager.py` for level select / reset progress
  - `scripts/settings.py` for persisted unlock flags
