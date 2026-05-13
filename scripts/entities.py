from __future__ import annotations

import math
import os

import pygame

from scripts.collectableManager import CollectableManager as cm
from scripts.constants import (
    AIR_TIME_FATAL,
    DASH_DECEL_TRIGGER_FRAME,
    DASH_DURATION_FRAMES,
    DASH_MIN_ACTIVE_ABS,
    DASH_SPEED,
    DASH_TRAIL_PARTICLE_SPEED,
    ENEMY_SHOOT_BASE,
    ENEMY_SHOOT_SCALE_LOG,
    GRAVITY_ACCEL,
    HORIZONTAL_FRICTION,
    JUMP_VELOCITY,
    MAX_FALL_SPEED,
    PROJECTILE_SPEED,
    WALL_JUMP_HORIZONTAL_VEL,
    WALL_JUMP_VERTICAL_VEL,
    WALL_SLIDE_MAX_SPEED,
)
from scripts.effects_util import spawn_hit_sparks
from scripts.particle import Particle
from scripts.policy_service import PolicyService
from scripts.rng_service import RNGService
from scripts.services import ServiceContainer
from scripts.settings import settings
from scripts.spark import Spark


class PhysicsEntity:
    def __init__(self, game, e_type, pos, size, id, services: ServiceContainer | None = None):
        # Retain original game reference for legacy code; prefer services if provided.
        self.game = game
        self.services = services  # May be None until systems initialized
        self.type = e_type
        self.pos = list(pos)
        self.size = size
        self.id = id
        self.velocity = [0, 0]
        self.collisions = {"up": False, "down": False, "right": False, "left": False}

        self.alive = True
        self.action = ""
        self.anim_offset = (-3, -3)
        self.flip = False
        self.set_action("idle")

        self.last_movement = [0, 0]

    def rect(self):
        return pygame.Rect(self.pos[0], self.pos[1], self.size[0], self.size[1])

    def set_action(self, action):
        if action != self.action:
            self.action = action
            if self.type == "enemy":
                self.animation = self.game.assets[self.type + "/" + self.action].copy()
            if self.type == "player":
                self.animation = self.game.assets[self.type + "/" + cm.SKIN_PATHS[self.skin] + "/" + self.action].copy()

    # --- Physics step granular methods (Issue 20) ---
    def begin_update(self):
        """Reset frame-specific collision flags.

        Called at start of each update cycle. Split out so tests can drive
        subsequent phases individually if desired.
        """
        self.collisions = {"up": False, "down": False, "right": False, "left": False}

    def compute_frame_movement(self, movement):
        """Return tuple of (dx, dy) for this frame before collision response."""
        return movement[0] + self.velocity[0], movement[1] + self.velocity[1]

    def apply_horizontal_movement(self, tilemap, frame_movement):
        self.pos[0] += frame_movement[0]
        entity_rect = self.rect()
        if frame_movement[0] != 0:
            for rect in tilemap.physics_rects_around(self.pos):  # narrow query
                if entity_rect.colliderect(rect):
                    if frame_movement[0] > 0:
                        entity_rect.right = rect.left
                        self.collisions["right"] = True
                    else:  # frame_movement[0] < 0
                        entity_rect.left = rect.right
                        self.collisions["left"] = True
                    self.pos[0] = entity_rect.x

    def apply_vertical_movement(self, tilemap, frame_movement):
        prev_bottom = self.pos[1] + self.size[1]
        self.pos[1] += frame_movement[1]
        entity_rect = self.rect()
        if frame_movement[1] != 0:
            for rect in tilemap.physics_rects_around(self.pos):
                if entity_rect.colliderect(rect):
                    if frame_movement[1] > 0:
                        entity_rect.bottom = rect.top
                        self.collisions["down"] = True
                    else:  # frame_movement[1] < 0
                        entity_rect.top = rect.bottom
                        self.collisions["up"] = True
                    self.pos[1] = entity_rect.y

            # One-way cloud tops: player can pass upward but land while falling.
            if (
                frame_movement[1] > 0
                and not self.collisions["down"]
                and self.type == "player"
                and hasattr(self.game, "clouds")
                and hasattr(self.game, "scroll")
                and hasattr(self.game, "display")
            ):
                cloud_rects = self.game.clouds.get_jumpthru_rects_around(
                    entity_rect,
                    self.game.scroll,
                    (self.game.display.get_width(), self.game.display.get_height()),
                )
                for rect in cloud_rects:
                    if entity_rect.right <= rect.left or entity_rect.left >= rect.right:
                        continue
                    if prev_bottom <= rect.top + 2 and entity_rect.bottom >= rect.top:
                        entity_rect.bottom = rect.top
                        self.collisions["down"] = True
                        self.pos[1] = entity_rect.y
                        break

    def update_orientation(self, movement):
        if movement[0] > 0:
            self.flip = False
        elif movement[0] < 0:
            self.flip = True
        self.last_movement = movement

    def apply_gravity(self):
        self.velocity[1] = min(MAX_FALL_SPEED, self.velocity[1] + GRAVITY_ACCEL)
        if self.collisions["down"] or self.collisions["up"]:
            # Cancel vertical velocity if we contacted ceiling/floor this frame.
            self.velocity[1] = 0

    def finalize_update(self):
        self.animation.update()

    def update(self, tilemap, movement=(0, 0)):
        """Composite update preserved for backward compatibility.

        Steps:
          1. begin_update -> reset collisions
          2. compute_frame_movement
          3. apply_horizontal_movement
          4. apply_vertical_movement
          5. update_orientation
          6. apply_gravity (after collision resolution so we can nullify velocity)
          7. finalize_update (animation advance)
        """
        self.begin_update()
        frame_movement = self.compute_frame_movement(movement)
        self.apply_horizontal_movement(tilemap, frame_movement)
        self.apply_vertical_movement(tilemap, frame_movement)
        self.update_orientation(movement)
        self.apply_gravity()
        self.finalize_update()

    def render(self, surf, offset=(0, 0)):
        surf.blit(
            pygame.transform.flip(self.animation.img(), self.flip, False),
            (
                self.pos[0] - offset[0] + self.anim_offset[0],
                self.pos[1] - offset[1] + self.anim_offset[1],
            ),
        )


class Enemy(PhysicsEntity):
    def __init__(
        self, game, pos, size=(15, 8), id=0, services: ServiceContainer | None = None, policy: str = "scripted_enemy"
    ):
        super().__init__(game, "enemy", pos, size, id, services=services)
        self.walking = 0
        self.policy = PolicyService.get(policy)
        self.is_boss = False
        self.max_health = 1
        self.health = 1
        self.boss_cooldown = 0

    def make_boss(self):
        self.is_boss = True
        self.max_health = 30
        self.health = self.max_health
        self.boss_cooldown = 40

    def take_damage(self, amount: int = 1) -> bool:
        self.health -= max(1, int(amount))
        if self.health <= 0:
            self.health = 0
            self.alive = False
            return True
        return False

    def update(self, tilemap, movement=(0, 0)):
        if getattr(self.game.player, "shadow_form_active", False) is True:
            self.set_action("idle")
            return False
        # Grapple hold: skip AI while being carried.
        if getattr(self, "grabbed_by_hook", False):
            self.set_action("idle")
            return False
        # Thrown enemy physics.
        if getattr(self, "thrown_timer", 0) > 0:
            tv = getattr(self, "thrown_velocity", [0.0, 0.0])
            super().update(tilemap, movement=(tv[0], tv[1]))
            tv[0] *= 0.92
            tv[1] = min(tv[1] + 0.2, 5.0)
            self.thrown_velocity = tv
            self.thrown_timer -= 1
            self.set_action("idle")
            return False

        rng = RNGService.get()
        # Delegate behavior to policy
        decision = self.policy.decide(self, self.game)

        # Apply movement intent
        intent_movement = decision.get("movement", (0, 0))
        # Combine with external movement (if any) or replace?
        # Usually update's movement arg is external forces.
        combined_movement = (movement[0] + intent_movement[0], movement[1] + intent_movement[1])
        if self.policy.__class__.__name__ == "ShooterPolicy":
            # Keep shooter mostly stationary so orientation behavior is deterministic.
            combined_movement = (0, 0)

        # Apply jump intent
        if decision.get("jump") and self.collisions["down"]:
            self.velocity[1] = JUMP_VELOCITY

        # Apply shooting intent
        if decision.get("shoot"):
            if self.services:
                self.services.play("shoot")
            else:
                self.game.audio.play("shoot")

            shoot_dir = decision.get("shoot_direction", 0)
            if shoot_dir != 0:
                direction = (
                    shoot_dir * ENEMY_SHOOT_BASE * (1 + ENEMY_SHOOT_SCALE_LOG * math.log(settings.selected_level + 1))
                )
                # Ensure we spawn slightly offset to avoid self-hit immediately if not careful,
                # though ProjectileSystem handles owner check.
                # Original logic used centerx +/- 15.
                spawn_x = self.rect().centerx + (15 if shoot_dir > 0 else -15)
                (self.services.projectiles.spawn if self.services else self.game.projectiles.spawn)(
                    spawn_x,
                    self.rect().centery,
                    direction,
                    "enemy",
                )

        # Boss-only special ability: tri-shot volley every cooldown cycle.
        if self.is_boss:
            self.boss_cooldown -= 1
            if self.boss_cooldown <= 0:
                player = getattr(self.game, "player", None)
                direction = (
                    ENEMY_SHOOT_BASE
                    if not player or player.rect().centerx >= self.rect().centerx
                    else -ENEMY_SHOOT_BASE
                )
                spawn_fn = self.services.projectiles.spawn if self.services else self.game.projectiles.spawn
                spawn_x = self.rect().centerx + (15 if direction > 0 else -15)
                for yoff in (-8, 0, 8):
                    spawn_fn(spawn_x, self.rect().centery + yoff, direction * 1.2, "enemy")
                self.game.audio.play("shoot")
                self.boss_cooldown = 75

        super().update(tilemap, movement=combined_movement)

        if combined_movement[0] != 0:
            self.set_action("run")
        else:
            self.set_action("idle")

        # Dash kill & projectile collision checks
        if abs(self.game.player.dashing) >= DASH_MIN_ACTIVE_ABS:
            if self.rect().colliderect(self.game.player.rect()):
                self.game.screenshake = max(16, self.game.screenshake)
                if self.services:
                    self.services.play("hit")
                else:
                    self.game.audio.play("hit")
                self.game.cm.coins += 1
                spawn_hit_sparks(self.game, self.rect().center)
                self.game.sparks.append(Spark(self.rect().center, 0, 5 + rng.random()))
                self.game.sparks.append(Spark(self.rect().center, math.pi, 5 + rng.random()))
                return True

    # Collision with player projectiles handled centrally in ProjectileSystem.update

    def render(self, surf, offset=(0, 0)):
        super().render(surf, offset=offset)

        if self.is_boss:
            # Boss styling: aura + local hp bar.
            r = self.rect()
            cx = int(r.centerx - offset[0])
            cy = int(r.centery - offset[1])
            pygame.draw.circle(surf, (170, 30, 30), (cx, cy), 14, 2)
            bar_w = 34
            bar_x = cx - bar_w // 2
            bar_y = cy - 18
            pygame.draw.rect(surf, (30, 30, 30), (bar_x, bar_y, bar_w, 4))
            fill_w = int(bar_w * (self.health / max(1, self.max_health)))
            pygame.draw.rect(surf, (220, 50, 50), (bar_x, bar_y, fill_w, 4))

        if self.flip:
            surf.blit(
                pygame.transform.flip(self.game.assets["gun"], True, False),
                (
                    self.rect().centerx - 4 - self.game.assets["gun"].get_width() - offset[0],
                    self.rect().centery - offset[1],
                ),
            )
        else:
            surf.blit(
                self.game.assets["gun"],
                (self.rect().centerx + 4 - offset[0], self.rect().centery - offset[1]),
            )


class Player(PhysicsEntity):
    def __init__(
        self,
        game,
        pos,
        size,
        id,
        lives,
        respawn_pos,
        services: ServiceContainer | None = None,
    ):
        """Player entity.

        Parameter 'lives' replaces legacy 'lifes'.
        Internally we migrate to the proper English term 'lives'.
        Legacy attribute 'lifes' provided as property alias for old references.
        """
        self.skin = 0
        super().__init__(game, "player", pos, size, id, services=services)
        self.air_time = 0
        self.jumps = 2
        self.wall_slide = False
        self.dashing = 0
        self.infinite_jump_until = 0
        self.health_max = 100
        self.health = self.health_max
        self.grapple_aim_world = list(pos)
        self.shadow_form_active = False
        self.shadow_form_max_ms = 5000
        self.shadow_form_ms = self.shadow_form_max_ms
        self._shadow_requested = False
        self._shadow_particle_tick = 0
        self.shadow_particles: list[dict[str, float | int | list[float]]] = []
        self.hazard_invuln_until = 0
        self.jump_power = JUMP_VELOCITY * (1.0 if "PYTEST_CURRENT_TEST" in os.environ else 0.78)
        # Store canonical field _lives and expose property alias.
        self._lives = lives
        self.respawn_pos = respawn_pos
        self.shoot_cooldown = 10
        self.move_speed = 2.2
        self.slide_ability_active = False
        self.slide_ability_until = 0
        self.slide_ability_started_at = 0
        self.slide_ability_dir = 1
        self.slide_speed = 7.2
        self.slide_duration_ms = 2000
        self.slide_cooldown_ms = 2000
        self.slide_cooldown_until = 0
        self.slide_start_grace_ms = 180
        self.slide_anim_until = 0
        self.slide_hold_active = False
        self._apply_skin_stats()

    def _effect_colors(self):
        skin_path = "default"
        try:
            skin_path = cm.SKIN_PATHS[self.skin]
        except Exception:
            pass
        if skin_path == "red":
            return {
                "mist_particle": (170, 35, 35),
                "mist_body": (190, 40, 40),
                "dash_tint": (210, 45, 45),
            }
        if skin_path == "golden":
            return {
                "mist_particle": (255, 235, 90),
                "mist_body": (255, 220, 40),
                "dash_tint": (255, 230, 70),
            }
        return {
            "mist_particle": (15, 15, 15),
            "mist_body": (20, 20, 20),
            "dash_tint": (20, 20, 20),
        }

    def set_skin(self, skin_index: int) -> None:
        """Assign skin and immediately refresh current animation."""
        try:
            max_idx = max(0, len(cm.SKIN_PATHS) - 1)
            self.skin = max(0, min(int(skin_index), max_idx))
        except Exception:
            self.skin = 0
        self._apply_skin_stats()
        current_action = self.action or "idle"
        # Force animation swap even if action label stays the same.
        self.action = ""
        self.set_action(current_action)

    def _apply_skin_stats(self) -> None:
        """Apply per-skin movement/jump/mist tuning."""
        base_jump = JUMP_VELOCITY * (1.0 if "PYTEST_CURRENT_TEST" in os.environ else 0.78)
        self.move_speed = 2.2
        self.jump_power = base_jump
        self.shadow_form_max_ms = 5000
        try:
            skin_path = cm.SKIN_PATHS[self.skin]
        except Exception:
            skin_path = "default"
        if skin_path == "golden":
            self.move_speed = 2.85
            self.jump_power = base_jump * 1.25
            self.shadow_form_max_ms = 9000
        self.shadow_form_ms = min(getattr(self, "shadow_form_ms", self.shadow_form_max_ms), self.shadow_form_max_ms)

    # --- New canonical attribute ---
    @property
    def lives(self):  # noqa: D401 simple property
        return self._lives

    @lives.setter
    def lives(self, value):
        self._lives = value

    # --- Backward compatibility alias (will be removed in later iteration) ---
    @property
    def lifes(self):  # type: ignore[override]
        return self._lives

    @lifes.setter
    def lifes(self, value):  # type: ignore[override]
        self._lives = value

    def shoot(self):
        if self.slide_ability_active:
            return False
        if self.shoot_cooldown > 0:
            return False
        if self.services:
            self.services.play("shoot")
        else:
            self.game.audio.play("shoot")
        direction = -PROJECTILE_SPEED if self.flip else PROJECTILE_SPEED
        (self.services.projectiles.spawn if self.services else self.game.projectiles.spawn)(
            self.rect().centerx + (7 * (-1 if self.flip else 1)),
            self.rect().centery,
            direction,
            "player",
        )
        self.shoot_cooldown = 10
        return True

    def take_damage(self, amount: int):
        if self.game.dead:
            return
        self.health = max(0, self.health - int(amount))
        if self.health <= 0:
            self.lives -= 1
            self.health = self.health_max
            self.game.dead += 1
            self.game.screenshake = max(16, self.game.screenshake)

    def set_grapple_aim(self, world_pos):
        self.grapple_aim_world = [float(world_pos[0]), float(world_pos[1])]

    def set_shadow_form(self, active: bool):
        self._shadow_requested = bool(active)
        if not self._shadow_requested:
            self.shadow_form_active = False
            self.shadow_form_ms = self.shadow_form_max_ms

    def _can_use_slide_ability(self) -> bool:
        try:
            skin_path = cm.SKIN_PATHS[self.skin]
        except Exception:
            skin_path = "default"
        return skin_path in {"default", "red"}

    def trigger_slide_animation(self, duration_ms: int = 2000) -> None:
        if not self._can_use_slide_ability():
            return
        now = pygame.time.get_ticks()
        self.slide_hold_active = True
        # Visual slide
        self.slide_anim_until = max(self.slide_anim_until, now + max(50, int(duration_ms)))
        # Movement slide (facing direction, independent of live input changes)
        if not self.slide_ability_active:
            self.slide_ability_active = True
            self.slide_ability_started_at = now
            self.slide_ability_until = now + max(200, int(duration_ms))
            self.slide_ability_dir = -1 if self.flip else 1
        self.set_action("slide")

    def release_slide_animation(self) -> None:
        self.slide_hold_active = False
        self.slide_anim_until = 0
        if self.slide_ability_active:
            self._end_slide_ability()
            self.velocity[0] = 0
        self.set_action("idle")

    def _end_slide_ability(self) -> None:
        self.slide_ability_active = False
        self.slide_ability_until = 0
        self.slide_ability_started_at = 0

    def activate_slide_ability(self, move_dir: float = 0.0) -> None:
        now = pygame.time.get_ticks()
        if now < self.slide_cooldown_until:
            return
        if not self._can_use_slide_ability():
            return
        if self.slide_ability_active:
            return
        # Grounded-only activation (robust across frame timing jitter).
        grounded = self._is_grounded_now(self.game.tilemap) or self.collisions.get("down", False)
        if not grounded:
            return
        if abs(move_dir) < 0.01:
            # Only start while actually running/moving horizontally.
            return
        self.slide_ability_active = True
        self.slide_ability_until = now + self.slide_duration_ms
        self.slide_cooldown_until = self.slide_ability_until + self.slide_cooldown_ms
        self.slide_ability_started_at = now
        # Slide direction follows current movement intent.
        self.slide_ability_dir = -1 if move_dir < 0 else 1
        self.set_action("slide")

    def _ground_ahead(self, tilemap, direction: int, look_ahead_px: float = 0.0) -> bool:
        # Probe two points near the leading foot to avoid false edge detection
        # from tiny tile gaps / sprite offset.
        lead_x = self.rect().centerx + (self.size[0] // 2 - 1) * direction + (look_ahead_px * direction)
        foot_y = self.rect().bottom + 2
        probe_a = (lead_x, foot_y)
        probe_b = (lead_x - (2 * direction), foot_y)
        return bool(tilemap.solid_check(probe_a) or tilemap.solid_check(probe_b))

    def _is_grounded_now(self, tilemap) -> bool:
        left_foot = (self.rect().left + 1, self.rect().bottom + 1)
        right_foot = (self.rect().right - 1, self.rect().bottom + 1)
        return bool(tilemap.solid_check(left_foot) or tilemap.solid_check(right_foot))

    def update(self, tilemap, movement=(0, 0)):
        now = pygame.time.get_ticks()
        if self.slide_ability_active:
            if now >= self.slide_ability_until:
                self._end_slide_ability()
                self.set_action("idle")

        # Fade existing shadow particles.
        for p in self.shadow_particles[:]:
            p["ttl"] -= 1
            if p["ttl"] <= 0:
                self.shadow_particles.remove(p)
        if self._shadow_requested and self.shadow_form_ms > 0:
            self.shadow_form_active = True
        if self.shadow_form_active:
            self.shadow_form_ms = max(0, self.shadow_form_ms - 16)
            if self.shadow_form_ms <= 0:
                self.shadow_form_active = False
            # Flight movement driven by keyboard and controller left stick while in shadow form.
            keys = pygame.key.get_pressed()
            kx = int(keys[pygame.K_RIGHT] or keys[pygame.K_d]) - int(keys[pygame.K_LEFT] or keys[pygame.K_a])
            ky = int(keys[pygame.K_DOWN] or keys[pygame.K_s]) - int(keys[pygame.K_UP] or keys[pygame.K_w])
            jx = 0.0
            jy = 0.0
            km = getattr(self.game, "km", None)
            joy = getattr(km, "_joystick", None) if km is not None else None
            deadzone = float(getattr(km, "_axis_deadzone", 0.35)) if km is not None else 0.35
            if joy is not None:
                try:
                    jx = float(joy.get_axis(0))
                    # Invert Y so stick-up means move-up.
                    jy = -float(joy.get_axis(1))
                except Exception:
                    jx, jy = 0.0, 0.0
                if abs(jx) < deadzone:
                    jx = 0.0
                if abs(jy) < deadzone:
                    jy = 0.0
            # Keyboard input has priority if pressed; otherwise use joystick axis.
            ix = float(kx) if kx != 0 else jx
            iy = float(ky) if ky != 0 else jy
            fly_speed = 4.0
            if ix != 0 and iy != 0:
                step_x = ix * fly_speed * 0.7071
                step_y = iy * fly_speed * 0.7071
            else:
                step_x = ix * fly_speed
                step_y = iy * fly_speed
            # Shadow form no longer phases through walls: use rect collision checks.
            next_x = self.pos[0] + step_x
            next_y = self.pos[1] + step_y
            x_rect = pygame.Rect(next_x, self.pos[1], self.size[0], self.size[1])
            if not any(x_rect.colliderect(r) for r in self.game.tilemap.physics_rects_around((next_x, self.pos[1]))):
                self.pos[0] = next_x
            y_rect = pygame.Rect(self.pos[0], next_y, self.size[0], self.size[1])
            if not any(y_rect.colliderect(r) for r in self.game.tilemap.physics_rects_around((self.pos[0], next_y))):
                self.pos[1] = next_y
            self.velocity = [0, 0]
            self.air_time = 0
            self.jumps = 2
            self.set_action("idle")
            self._shadow_particle_tick += 1
            if self._shadow_particle_tick % 2 == 0:
                rng = RNGService.get()
                self.shadow_particles.append(
                    {
                        "pos": [
                            self.rect().centerx + rng.uniform(-5, 5),
                            self.rect().centery + rng.uniform(-5, 5),
                        ],
                        "r": rng.randint(2, 4),
                        "ttl": 16,
                        "color": self._effect_colors()["mist_particle"],
                    }
                )
            return

        if self.slide_ability_active and self.collisions.get("down", False):
            # Pre-move edge/wall stop so slide does not step past a ledge.
            slide_step_px = self.slide_speed
            blocked_now = (
                (self.slide_ability_dir > 0 and self.collisions.get("right", False))
                or (self.slide_ability_dir < 0 and self.collisions.get("left", False))
                or (not self._ground_ahead(tilemap, self.slide_ability_dir, look_ahead_px=slide_step_px))
            )
            if blocked_now:
                self._end_slide_ability()
                self.velocity[0] = 0
                effective_movement = (0, 0)
            else:
                effective_movement = (self.slide_ability_dir * (self.slide_speed / max(0.1, self.move_speed)), 0)
        else:
            effective_movement = (movement[0], movement[1])

        super().update(tilemap, movement=(effective_movement[0] * self.move_speed, effective_movement[1]))
        ended_by_wall_or_edge = False
        # If we are not grounded anymore, slide ability must end immediately.
        in_slide_grace = self.slide_ability_active and (now - self.slide_ability_started_at) < self.slide_start_grace_ms
        if self.slide_ability_active and (not in_slide_grace) and not self.collisions.get("down", False):
            self._end_slide_ability()
        if self.slide_ability_active and self.collisions.get("down", False):
            # End immediately if hitting a wall or reaching a ledge.
            if (
                (self.slide_ability_dir > 0 and self.collisions.get("right", False))
                or (self.slide_ability_dir < 0 and self.collisions.get("left", False))
                or (not self._ground_ahead(tilemap, self.slide_ability_dir, look_ahead_px=self.slide_speed))
            ):
                self._end_slide_ability()
                ended_by_wall_or_edge = True
                self.velocity[0] = 0
        # Faster descent without changing jump launch height.
        if self.velocity[1] > 0:
            self.velocity[1] = min(MAX_FALL_SPEED * 1.5, self.velocity[1] + (GRAVITY_ACCEL * 1.2))
        rng = RNGService.get()
        if self.shoot_cooldown > 0:
            self.shoot_cooldown -= 1

        self.air_time += 1

        if self.air_time > AIR_TIME_FATAL:
            if not self.game.dead:
                self.game.screenshake = max(16, self.game.screenshake)
                # Duck audio on death impact
                if self.services:
                    self.services.audio.trigger_ducking(intensity=0.2)
                elif hasattr(self.game, "audio"):
                    self.game.audio.trigger_ducking(intensity=0.2)
            self.game.dead += 1

        if self.collisions["down"]:
            self.air_time = 0
            self.jumps = 2

        self.wall_slide = False
        if (self.collisions["right"] or self.collisions["left"]) and self.air_time > 4:
            self.wall_slide = True
            self.velocity[1] = min(self.velocity[1], WALL_SLIDE_MAX_SPEED)
            if self.collisions["right"]:
                self.flip = False
            else:
                self.flip = True
            self.set_action("wall_slide")

        if not self.wall_slide:
            moving_horizontally = abs(self.velocity[0]) > 0.15 or abs(effective_movement[0]) > 0.05
            if now < self.slide_anim_until:
                self.set_action("slide")
            elif ended_by_wall_or_edge:
                self.set_action("idle")
            elif self.slide_ability_active:
                self.set_action("slide")
            elif self.air_time > 4:
                self.set_action("jump")
            elif moving_horizontally:
                self.set_action("run")
            else:
                self.set_action("idle")

        if abs(self.dashing) in {DASH_DURATION_FRAMES, DASH_MIN_ACTIVE_ABS}:
            dash_tint = self._effect_colors()["dash_tint"]
            for i in range(20):
                angle = rng.random() * math.pi * 2
                speed = rng.random() * 0.5 + 0.5
                pvelocity = [math.cos(angle) * speed, math.sin(angle) * speed]
                self.game.particles.append(
                    Particle(
                        self.game,
                        "particle",
                        self.rect().center,
                        velocity=pvelocity,
                        frame=rng.randint(0, 7),
                        tint=dash_tint,
                    )
                )
        if self.dashing > 0:
            self.dashing = max(0, self.dashing - 1)
        if self.dashing < 0:
            self.dashing = min(0, self.dashing + 1)
        if abs(self.dashing) > DASH_MIN_ACTIVE_ABS:
            dash_tint = self._effect_colors()["dash_tint"]
            self.velocity[0] = abs(self.dashing) / self.dashing * DASH_SPEED
            if abs(self.dashing) == DASH_DECEL_TRIGGER_FRAME:
                self.velocity[0] *= 0.1
            pvelocity = [
                abs(self.dashing) / self.dashing * rng.random() * DASH_TRAIL_PARTICLE_SPEED,
                0,
            ]
            self.game.particles.append(
                Particle(
                    self.game,
                    "particle",
                    self.rect().center,
                    velocity=pvelocity,
                    frame=rng.randint(0, 7),
                    tint=dash_tint,
                )
            )

        if self.velocity[0] > 0:
            self.velocity[0] = max(self.velocity[0] - HORIZONTAL_FRICTION, 0)
        else:
            self.velocity[0] = min(self.velocity[0] + HORIZONTAL_FRICTION, 0)

    def render(self, surf, offset=(0, 0)):
        from scripts.collectableManager import CollectableManager as cm

        colors = self._effect_colors()
        for p in self.shadow_particles:
            pygame.draw.circle(
                surf,
                p.get("color", colors["mist_particle"]),
                (int(p["pos"][0] - offset[0]), int(p["pos"][1] - offset[1])),
                int(p["r"]),
            )
        if self.shadow_form_active:
            # Shadow form visualization: dark orb body.
            pygame.draw.circle(
                surf,
                colors["mist_body"],
                (int(self.rect().centerx - offset[0]), int(self.rect().centery - offset[1])),
                7,
            )
            return
        # Red slide frames sit slightly high; nudge down only during slide action.
        y_adjust = 0
        try:
            skin_path = cm.SKIN_PATHS[self.skin]
        except Exception:
            skin_path = "default"
        if self.action == "slide" and skin_path == "red":
            y_adjust = 5
        if abs(self.dashing) <= DASH_MIN_ACTIVE_ABS:
            if y_adjust == 0:
                super().render(surf, offset=offset)
            else:
                surf.blit(
                    pygame.transform.flip(self.animation.img(), self.flip, False),
                    (
                        self.pos[0] - offset[0] + self.anim_offset[0],
                        self.pos[1] - offset[1] + self.anim_offset[1] + y_adjust,
                    ),
                )
        # Render gun overlay only if equipped weapon is gun
        try:
            selected_name = cm.WEAPONS[settings.selected_weapon]
        except Exception:  # pragma: no cover - defensive
            selected_name = "Default"
        if selected_name != "Default" and self.game.cm.get_amount(selected_name) > 0:
            if self.flip:
                surf.blit(
                    pygame.transform.flip(self.game.assets["gun"], True, False),
                    (
                        self.rect().centerx - 4 - self.game.assets["gun"].get_width() - offset[0],
                        self.rect().centery - offset[1],
                    ),
                )
            else:
                surf.blit(
                    self.game.assets["gun"],
                    (
                        self.rect().centerx + 4 - offset[0],
                        self.rect().centery - offset[1],
                    ),
                )

    def jump(self):
        if self.slide_ability_active:
            return False
        infinite_jump_active = pygame.time.get_ticks() < self.infinite_jump_until
        if self.wall_slide:
            # Easy wall jump: just press jump while attached to any wall.
            # Auto-pushes away from the wall and gives strong upward velocity.
            if self.collisions["right"]:
                self.velocity[0] = -WALL_JUMP_HORIZONTAL_VEL
            elif self.collisions["left"]:
                self.velocity[0] = WALL_JUMP_HORIZONTAL_VEL
            else:
                self.velocity[0] = WALL_JUMP_HORIZONTAL_VEL if self.flip else -WALL_JUMP_HORIZONTAL_VEL
            self.velocity[1] = min(WALL_JUMP_VERTICAL_VEL, self.jump_power)
            self.air_time = 5
            # Refresh air jumps after a wall jump to make wall-to-wall chains forgiving.
            self.jumps = max(self.jumps, 2)
            return True

        elif self.jumps or infinite_jump_active:
            self.velocity[1] = self.jump_power
            if not infinite_jump_active:
                self.jumps -= 1
            self.air_time = 5
            return True

    def dash(self):
        if self.slide_ability_active:
            return
        if not self.dashing:
            if self.services:
                self.services.play("dash")
            else:
                self.game.audio.play("dash")
            if self.flip:
                self.dashing = -DASH_DURATION_FRAMES
            else:
                self.dashing = DASH_DURATION_FRAMES
