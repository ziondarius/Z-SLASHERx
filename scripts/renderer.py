"""Unified rendering pipeline (Issue 14).

This module centralizes frame composition order so that *all* entry
points (legacy `Game.run` and new `GameState.render`) follow the same
deterministic layering. This is a transitional orchestrator; future
iterations (Issues 15–18) will replace direct asset / effect access
with dedicated systems (AssetManager, ParticleSystem, etc.).

Layer Order (bottom -> top):
1. Clear primary off-screen buffers (game.display)
2. Background image -> display_2
3. World & entities (tiles, player, enemies, projectiles, particles) via UI helper
4. Transition / full-screen visual effects pre-compose (optional)
5. Compose game.display onto display_2
6. HUD / UI overlay (timer, level info, lives, coins, ammo, perf metrics)
7. Screen shake / post effects applied just before blitting to window

Design Notes:
- Keeps all mutation of game surfaces in one place.
- Optional `capture_sequence` parameter records executed high-level steps
  for tests (avoids brittle pixel sampling with live artwork).
- Avoids importing heavy modules at top-level when possible to reduce
  import side-effects during test collection.
"""

from __future__ import annotations

from typing import List, Optional

import pygame

try:  # Local logger (not mandatory for tests)
    from scripts.logger import get_logger  # type: ignore
except Exception:  # pragma: no cover - fallback when logger absent

    def get_logger(name: str):  # type: ignore
        class _Nop:
            def debug(self, *a, **k):
                pass

        return _Nop()


_log = get_logger("renderer")


class Renderer:
    """High-level frame orchestrator.

    Usage:
        r = Renderer(show_perf=True)
        r.render(game, window_surface)
    """

    def __init__(self, show_perf: bool = True) -> None:
        import os
        from scripts.perf_hud import PerformanceHUD  # local import

        log_file = os.environ.get("PERF_LOG_FILE")
        self.perf_hud = PerformanceHUD(enabled=show_perf, log_path=log_file)

    @property
    def last_frame_ms(self) -> float:
        return self.perf_hud.last_full_frame_ms

    def render(
        self,
        game,  # legacy Game object
        target_surface: pygame.Surface,
        capture_sequence: Optional[List[str]] = None,
    ) -> None:
        seq = capture_sequence
        # Start timing via HUD abstraction
        self.perf_hud.begin_frame()
        # 1. Clear primary off-screen buffer
        game.display.fill((0, 0, 0, 0))
        if seq is not None:
            seq.append("clear")

        # 2. Background
        game.display_2.blit(game.assets["background"], (0, 0))
        if seq is not None:
            seq.append("background")

        # 3. World & entities
        from scripts.ui import UI  # local import avoids cycles

        render_scroll = (int(game.scroll[0]), int(game.scroll[1]))
        # Render ghost before world to keep player on top
        replay_mgr = getattr(game, "replay", None)
        if replay_mgr:
            try:
                replay_mgr.render_ghost(game.display, render_scroll)
            except Exception:
                pass
        UI.render_game_elements(game, render_scroll)
        if seq is not None:
            seq.append("world")

        # 4. Transition
        from scripts.effects import Effects

        if game.transition:
            Effects.transition(game)
            if seq is not None:
                seq.append("effects_transition")

        # 5. Compose
        game.display_2.blit(game.display, (0, 0))
        if seq is not None:
            seq.append("compose")

        # 6. HUD:
        game_counts = {
            "players": len(getattr(game, "players", [])),
            "enemies": len(getattr(game, "enemies", [])),
            "projectiles": len(getattr(game, "projectiles", [])),
            "particles": len(getattr(game, "particles", [])),
            "sparks": len(getattr(game, "sparks", [])),
        }
        self._render_hud(game, game_counts=game_counts)
        if seq is not None:
            seq.append("hud")
        # Mark end of work segment (work portion excludes optional perf overlay drawing)
        self.perf_hud.end_work_segment()
        state_ref = getattr(game, "state_ref", None)
        if getattr(state_ref, "perf_enabled", False):
            # Add tile type counts (throttled via overlay rebuild) without heavy per-frame cost
            try:
                if hasattr(game, "tilemap") and hasattr(game.tilemap, "get_type_counts"):
                    self.perf_hud._game_counts = {
                        **getattr(self.perf_hud, "_game_counts", {}),  # existing entity counts
                        **{f"tile:{k}": v for k, v in game.tilemap.get_type_counts().items()},
                    }
                if hasattr(game, "cm") and hasattr(game.cm, "get_collectable_counts"):
                    self.perf_hud._game_counts = {
                        **getattr(self.perf_hud, "_game_counts", {}),
                        **game.cm.get_collectable_counts(),
                    }
            except Exception:
                pass
            # Render performance overlay (timings + counts incl. tiles)
            self.perf_hud.render(game.display_2, x=5, y=game.BASE_H - 170)
            if seq is not None and self.perf_hud.last_sample:
                seq.append("perf")

        # 7. Post effects (screenshake) then present
        Effects.screenshake(game)
        if seq is not None:
            seq.append("effects_post")

        if game.display_2.get_size() != target_surface.get_size():
            scaled = pygame.transform.scale(game.display_2, target_surface.get_size())
            target_surface.blit(scaled, (0, 0))
        else:
            target_surface.blit(game.display_2, (0, 0))
        if seq is not None:
            seq.append("blit")
        # Finalize full frame timing (sample available next frame)
        self.perf_hud.end_frame(clock=getattr(game, "clock", None))

    # Full frame time available next frame via self._last_frame_ms

    def _render_hud(self, game, game_counts=None) -> None:
        from scripts.ui import UI

        UI.render_game_ui_element(game.display_2, f"{game.timer.text}", game.BASE_W - 70, 5)
        UI.render_game_ui_element(game.display_2, f"{game.timer.best_time_text}", game.BASE_W - 70, 15)
        UI.render_game_ui_element(game.display_2, f"Level: {game.level}", game.BASE_W // 2 - 40, 5)
        if getattr(game, "player", None):
            lives = getattr(game.player, "lives", getattr(game.player, "lifes", 0))
            UI.render_game_ui_element(game.display_2, f"Lives: {lives}", 5, 5)
            # Health bar
            hp = int(getattr(game.player, "health", 100))
            hp_max = max(1, int(getattr(game.player, "health_max", 100)))
            bx, by, bw, bh = 5, 36, 120, 10
            pygame.draw.rect(game.display_2, (40, 40, 40), (bx, by, bw, bh))
            fill_w = int(bw * max(0.0, min(1.0, hp / hp_max)))
            color = (50, 220, 90) if hp > hp_max * 0.4 else (230, 190, 45) if hp > hp_max * 0.2 else (220, 50, 50)
            pygame.draw.rect(game.display_2, color, (bx, by, fill_w, bh))
            pygame.draw.rect(game.display_2, (255, 255, 255), (bx, by, bw, bh), 1)
            UI.render_menu_ui_element(game.display_2, f"HP: {hp}/{hp_max}", game.BASE_W - 170, 24)
            # Timers for power states
            now = pygame.time.get_ticks()
            apple_ms = max(0, int(getattr(game.player, "infinite_jump_until", 0)) - now)
            UI.render_game_ui_element(game.display_2, f"Apple Jump: {apple_ms / 1000:.1f}s", 5, 50)
            if getattr(game.player, "shadow_form_active", False):
                mist_ms = max(0, int(getattr(game.player, "shadow_form_ms", 0)))
                UI.render_game_ui_element(game.display_2, f"Black Mist: {mist_ms / 1000:.1f}s", 5, 60)
            else:
                UI.render_game_ui_element(game.display_2, "Black Mist: Hold Dash 2s", 5, 60)
            gold_ms = max(0, int(getattr(game.player, "golden_apple_until", 0)) - now)
            if gold_ms > 0:
                UI.render_game_ui_element(game.display_2, f"Golden Apple: {gold_ms / 1000:.1f}s", 5, 70)
        self._render_minimap(game)
        UI.render_game_ui_element(game.display_2, f"${game.cm.coins}", 5, 15)
        UI.render_game_ui_element(game.display_2, f"Ammo:  {game.cm.ammo}", 5, 25)
        # Boss health bar (if present on this level)
        boss = None
        for e in getattr(game, "enemies", []):
            if getattr(e, "is_boss", False):
                boss = e
                break
        if boss is not None:
            bar_w = 220
            bar_h = 12
            bx = game.BASE_W // 2 - bar_w // 2
            by = 22
            pygame.draw.rect(game.display_2, (20, 20, 20), (bx, by, bar_w, bar_h))
            fill_w = int(bar_w * (boss.health / max(1, boss.max_health)))
            pygame.draw.rect(game.display_2, (220, 60, 60), (bx, by, fill_w, bar_h))
            pygame.draw.rect(game.display_2, (255, 255, 255), (bx, by, bar_w, bar_h), 1)
            UI.render_game_ui_element(game.display_2, "BOSS", game.BASE_W // 2 - 16, 12)
        # Pass counts to performance HUD via perf_hud.render call later
        if game_counts is not None:
            try:
                # Attach for perf overlay rendering (perf_hud.render uses UI.render_perf_overlay)
                self.perf_hud._game_counts = game_counts  # type: ignore[attr-defined]
            except Exception:
                pass

    def _render_minimap(self, game) -> None:
        bounds = getattr(game, "_minimap_bounds", None)
        if not bounds or not getattr(game, "player", None):
            return

        min_x, min_y, max_x, max_y = bounds
        span_x = max(1, max_x - min_x)
        span_y = max(1, max_y - min_y)

        map_w = 140
        map_h = 88
        pad = 6
        panel_x = game.BASE_W - map_w - 8
        panel_y = 28

        panel = pygame.Surface((map_w, map_h), pygame.SRCALPHA)
        panel.fill((10, 20, 30, 170))
        pygame.draw.rect(panel, (180, 210, 240), (0, 0, map_w, map_h), 1)

        def world_to_minimap(wx: float, wy: float) -> tuple[int, int]:
            nx = (wx - min_x) / span_x
            ny = (wy - min_y) / span_y
            mx = int(pad + max(0.0, min(1.0, nx)) * (map_w - 2 * pad))
            my = int(pad + max(0.0, min(1.0, ny)) * (map_h - 2 * pad))
            return mx, my

        # Flag marker(s)
        for f in getattr(game, "flags", []):
            fx, fy = world_to_minimap(f.centerx, f.centery)
            pygame.draw.circle(panel, (235, 60, 60), (fx, fy), 3)

        # Best-record ghost marker (yellow), when available.
        replay_mgr = getattr(game, "replay", None)
        ghost = getattr(replay_mgr, "ghost", None) if replay_mgr else None
        ghost_entity = getattr(ghost, "entity", None) if ghost else None
        ghost_pos = getattr(ghost_entity, "pos", None) if ghost_entity else None
        if ghost_pos is not None and len(ghost_pos) >= 2:
            gx, gy = world_to_minimap(float(ghost_pos[0]), float(ghost_pos[1]))
            pygame.draw.circle(panel, (255, 230, 70), (gx, gy), 3)

        # Player marker
        p = game.player.rect().center
        px, py = world_to_minimap(p[0], p[1])
        pygame.draw.circle(panel, (60, 150, 255), (px, py), 3)
        pygame.draw.circle(panel, (20, 20, 20), (px, py), 4, 1)

        # Camera viewport outline for orientation.
        view_w = getattr(game.display, "get_width", lambda: 1)()
        view_h = getattr(game.display, "get_height", lambda: 1)()
        sx, sy = getattr(game, "scroll", [0, 0])
        x0, y0 = world_to_minimap(sx, sy)
        x1, y1 = world_to_minimap(sx + view_w, sy + view_h)
        vx = min(x0, x1)
        vy = min(y0, y1)
        vw = max(2, abs(x1 - x0))
        vh = max(2, abs(y1 - y0))
        pygame.draw.rect(panel, (120, 120, 140), (vx, vy, vw, vh), 1)

        game.display_2.blit(panel, (panel_x, panel_y))


__all__ = ["Renderer"]
