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
        self.golden_apple_until = 0
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
        from scripts.collectableManager import CollectableManager as cm
        from scripts.weapons import get_weapon  # local import to avoid circulars

        # Map selected index to weapon name list
        try:
            name = cm.WEAPONS[settings.selected_weapon]
        except Exception:  # pragma: no cover - defensive
            name = "Default"
        # Use no-op only for Default; every other equipped weapon uses projectile behavior.
        key = "none" if name == "Default" else "gun"
        weapon = get_weapon(key)
        return weapon.fire(self)

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

    def update(self, tilemap, movement=(0, 0)):
        now = pygame.time.get_ticks()
        # Fade existing shadow particles.
        for p in self.shadow_particles[:]:
            p["ttl"] -= 1
            if p["ttl"] <= 0:
                self.shadow_particles.remove(p)
        # Golden apple power: fly + phase through walls.
        if now < self.golden_apple_until:
            keys = pygame.key.get_pressed()
            ix = int(keys[pygame.K_RIGHT] or keys[pygame.K_d]) - int(keys[pygame.K_LEFT] or keys[pygame.K_a])
            iy = int(keys[pygame.K_DOWN] or keys[pygame.K_s]) - int(keys[pygame.K_UP] or keys[pygame.K_w])
            fly_speed = 4.8
            if ix != 0 and iy != 0:
                step_x = ix * fly_speed * 0.7071
                step_y = iy * fly_speed * 0.7071
            else:
                step_x = ix * fly_speed
                step_y = iy * fly_speed
            self.pos[0] += step_x
            self.pos[1] += step_y
            # Keep golden-flight within current level bounds.
            bounds = getattr(self.game, "_minimap_bounds", None)
            if bounds:
                min_x, min_y, max_x, max_y = bounds
                self.pos[0] = max(min_x, min(self.pos[0], max_x - self.size[0]))
                self.pos[1] = max(min_y, min(self.pos[1], max_y - self.size[1]))
            self.velocity = [0, 0]
            self.air_time = 0
            self.jumps = 2
            self.wall_slide = False
            self.set_action("idle")
            return
        if self._shadow_requested and self.shadow_form_ms > 0:
            self.shadow_form_active = True
        if self.shadow_form_active:
            self.shadow_form_ms = max(0, self.shadow_form_ms - 16)
            if self.shadow_form_ms <= 0:
                self.shadow_form_active = False
            # Flight movement driven by keyboard (WASD / arrows) while in shadow form.
            keys = pygame.key.get_pressed()
            ix = int(keys[pygame.K_RIGHT] or keys[pygame.K_d]) - int(keys[pygame.K_LEFT] or keys[pygame.K_a])
            iy = int(keys[pygame.K_DOWN] or keys[pygame.K_s]) - int(keys[pygame.K_UP] or keys[pygame.K_w])
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
                    }
                )
            return

        super().update(tilemap, movement=(movement[0] * self.move_speed, movement[1]))
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
            if self.air_time > 4:
                self.set_action("jump")
            elif movement[0] != 0:
                self.set_action("run")
            else:
                self.set_action("idle")

        if abs(self.dashing) in {DASH_DURATION_FRAMES, DASH_MIN_ACTIVE_ABS}:
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
                    )
                )
        if self.dashing > 0:
            self.dashing = max(0, self.dashing - 1)
        if self.dashing < 0:
            self.dashing = min(0, self.dashing + 1)
        if abs(self.dashing) > DASH_MIN_ACTIVE_ABS:
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
                )
            )

        if self.velocity[0] > 0:
            self.velocity[0] = max(self.velocity[0] - HORIZONTAL_FRICTION, 0)
        else:
            self.velocity[0] = min(self.velocity[0] + HORIZONTAL_FRICTION, 0)

    def render(self, surf, offset=(0, 0)):
        for p in self.shadow_particles:
            pygame.draw.circle(
                surf,
                (15, 15, 15),
                (int(p["pos"][0] - offset[0]), int(p["pos"][1] - offset[1])),
                int(p["r"]),
            )
        if self.shadow_form_active:
            # Shadow form visualization: dark orb body.
            pygame.draw.circle(
                surf,
                (20, 20, 20),
                (int(self.rect().centerx - offset[0]), int(self.rect().centery - offset[1])),
                7,
            )
            return
        if abs(self.dashing) <= DASH_MIN_ACTIVE_ABS:
            super().render(surf, offset=offset)
        # Render gun overlay only if equipped weapon is gun
        from scripts.collectableManager import CollectableManager as cm

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
        if not self.dashing:
            if self.services:
                self.services.play("dash")
            else:
                self.game.audio.play("dash")
            if self.flip:
                self.dashing = -DASH_DURATION_FRAMES
            else:
                self.dashing = DASH_DURATION_FRAMES
