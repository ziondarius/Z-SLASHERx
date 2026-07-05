# context #3

## 1. Game Loop State
- Framework: Pygame
- Entry point: `app.py`
- Screen resolution: `1280x720` windowed, fullscreen when `settings.fullscreen` is enabled
- Target FPS: `60 FPS` via `clock.tick(60)`
- Current engine setup:
  - `StateManager` drives the app flow
  - `InputRouter` converts raw input into state actions
  - `GameState` owns the legacy `Game` instance for gameplay
  - `PauseState` is pushed as an overlay when the player pauses

## 2. Core State Machine
- Active states in the codebase:
  - `MenuState`
  - `GameState`
  - `PauseState`
  - `LevelsState`
  - `SkinsState` / character select flow
  - `OptionsState`
- Global runtime state:
  - `settings.selected_level`
  - `settings.playable_levels`
  - `settings.selected_character`
  - `settings.selected_weapon`
  - `settings.fullscreen`
  - `settings.music_volume`
  - `settings.sound_volume`
  - `Game.level`
  - `Game.timer`
  - `Game.screenshake`
  - `Game.running`
  - `Game.players`
  - `Game.enemies`
  - `Game.projectiles`
  - `Game.clouds`
  - `Game.tilemap`

## 3. Assets & Paths
- Core images and animation paths currently mapped in `game.py`:
  - `data/images/background-big.png`
  - `data/images/entities/player.png`
  - `data/images/gun.png`
  - `data/images/arrow.png`
  - `data/images/projectile.png`
  - `data/images/exclamation_mark.png`
  - `data/images/question_mark.png`
  - `data/images/clouds/*`
  - `data/images/tiles/decor/*`
  - `data/images/tiles/grass/*`
  - `data/images/tiles/large_decor/*`
  - `data/images/tiles/stone/*`
  - `data/images/tiles/collectables/coin/*`
  - `data/images/collectables/apple/*`
  - `data/images/collectables/heart/*`
  - `data/images/tiles/collectables/flag/*`
- Player character animation folders discovered in the repo:
  - `data/images/entities/player/default/*`
  - `data/images/entities/player/red/*`
  - `data/images/entities/player/golden/*`
  - `data/images/entities/player/archer/*`
- Map files currently present:
  - `data/maps/0.json` through `data/maps/10.json`
- Audio keys currently used in gameplay:
  - `jump`
  - `shoot`
  - `hit`
  - `collect`

## 4. Completed Features
- Player movement, jumping, wall sliding, and gravity are working
- Cloud tiles behave like jump-through platforms
- Enemy patrol logic was updated to walk to edges and walls, then turn around
- Enemy alert behavior now shows `!` and `?` markers and keeps enemies inside their platform
- Arrow projectile support exists for the archer character
- Mirror Phantom exists for the golden character
- Default character has a timed speed boost / aura ability
- Red character can toggle enemy form
- The HUD now shows 4 heart lives instead of a health bar
- The heart pickup uses the same heart art and restores one heart
- The top-left edition label now reads `Character Edition v2.0`
- Progress now starts locked except for the first level

## 5. Current Bug / Task
- Current file: `scripts/ai/behaviors.py`
- Current class: `ScriptedEnemyPolicy`
- Current behavior being tuned:
  - smarter enemy pacing while alerted
  - comfortable firing distance with fallback facing if the platform is too tight
  - exclamation/question mark transitions when line of sight changes
- Related render support also lives in:
  - `scripts/entities.py` for drawing the alert markers above enemies
