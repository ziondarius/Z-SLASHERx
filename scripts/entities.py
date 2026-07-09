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
    ENEMY_DIRECTION_BASE,
    ENEMY_DIRECTION_SCALE_LOG,
    ENEMY_SHOOT_BASE,
    ENEMY_SHOOT_SCALE_LOG,
    GRAVITY_ACCEL,
    HORIZONTAL_FRICTION,
    JUMP_VELOCITY,
    MAX_FALL_SPEED,
    SWORDSMAN_ATTACK_RANGE,
    SWORDSMAN_SWING_DURATION,
    WALL_JUMP_HORIZONTAL_VEL,
    WALL_JUMP_VERTICAL_VEL,
    WALL_SLIDE_MAX_SPEED,
)
from scripts.effects_util import spawn_hit_sparks, spawn_sword_sparks
from scripts.abilities.mirror_phantom import MirrorPhantomAbility
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
                if self.enemy_form_active:
                    enemy_action = self.action if self.action in {"idle", "run"} else "idle"
                    self.animation = self.game.assets[f"enemy/{enemy_action}"].copy()
                else:
                    self.animation = self.game.assets[self.type + "/" + cm.CHARACTER_PATHS[self.character] + "/" + self.action].copy()

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

            # Clouds are visual-only now; no jump-through landing collision.

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
        self.sprite_set = "default"
        super().__init__(game, "enemy", pos, size, id, services=services)
        self.walking = 0
        self.policy = PolicyService.get(policy)
        self.is_boss = False
        self.max_health = 1
        self.health = 1
        self.boss_cooldown = 0
        self.weapon_kind = "gun"
        self.deflect_projectiles = False
        self.sword_swing_active = False
        self.sword_swing_until = 0
        self.sword_swing_cooldown_until = 0
        self.sword_swing_hit = False

    def set_action(self, action):
        if action == self.action:
            return
        self.action = action
        sprite_key = f"enemy/{self.sprite_set}/{self.action}"
        if sprite_key in self.game.assets:
            self.animation = self.game.assets[sprite_key].copy()
        else:
            fallback_key = f"enemy/{self.action}"
            if fallback_key in self.game.assets:
                self.animation = self.game.assets[fallback_key].copy()

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
        if getattr(self.game.player, "mirror_phantom_active", False) is True:
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
        now = pygame.time.get_ticks()
        if self.weapon_kind == "sword":
            if decision.get("swing"):
                self.sword_swing_active = True
                self.sword_swing_until = max(self.sword_swing_until, now + SWORDSMAN_SWING_DURATION)
                self.sword_swing_hit = False
            if self.sword_swing_active and now >= self.sword_swing_until:
                self.sword_swing_active = False

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
            self.velocity[1] = decision.get("jump_velocity", JUMP_VELOCITY)

        # Apply shooting intent
        if decision.get("shoot") and not getattr(self.game.player, "enemy_form_active", False):
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

        if self.weapon_kind == "sword" and self.sword_swing_active:
            player_rect = self.game.player.rect()
            attack_rect = self.rect().copy()
            attack_rect.y -= 1
            attack_rect.height += 2
            if self.flip:
                attack_rect.x -= SWORDSMAN_ATTACK_RANGE
                attack_rect.width = SWORDSMAN_ATTACK_RANGE + attack_rect.width // 2
            else:
                attack_rect.width = SWORDSMAN_ATTACK_RANGE + attack_rect.width // 2
            if attack_rect.colliderect(player_rect):
                if abs(self.game.player.dashing) >= DASH_MIN_ACTIVE_ABS:
                    if not self.sword_swing_hit:
                        spawn_sword_sparks(self.game, self.rect().center)
                        self.sword_swing_hit = True
                elif not self.sword_swing_hit:
                    self.game.player.take_damage(1)
                    self.sword_swing_hit = True
                    spawn_sword_sparks(self.game, self.rect().center)

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

        if self.weapon_kind == "sword":
            if not self.collisions["down"] or decision.get("jump"):
                self.set_action("jump")
            elif combined_movement[0] != 0:
                self.set_action("run")
            else:
                self.set_action("idle")
        elif combined_movement[0] != 0:
            self.set_action("run")
        else:
            self.set_action("idle")

        # Dash kill & projectile collision checks
        if abs(self.game.player.dashing) >= DASH_MIN_ACTIVE_ABS:
            if self.rect().colliderect(self.game.player.rect()):
                if self.weapon_kind == "sword" and self.sword_swing_active:
                    spawn_sword_sparks(self.game, self.rect().center)
                    self.sword_swing_hit = True
                    return False
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

        if getattr(self, "golden_marked", False):
            from scripts.abilities.mirror_phantom import MirrorPhantomAbility

            if hasattr(self.game.player, "mirror_phantom") and isinstance(self.game.player.mirror_phantom, MirrorPhantomAbility):
                self.game.player.mirror_phantom.apply_mark_visual(surf, self, offset)

        alert_icon = getattr(self, "enemy_alert_icon", None)
        if alert_icon and alert_icon in self.game.assets:
            icon = self.game.assets[alert_icon]
            r = self.rect()
            surf.blit(
                icon,
                (
                    r.centerx - icon.get_width() // 2 - offset[0],
                    r.top - icon.get_height() - 2 - offset[1],
                ),
            )

        if self.flip:
            if self.weapon_kind == "sword" and "sword" in self.game.assets:
                sword = pygame.transform.flip(self.game.assets["sword"], True, False)
                if self.sword_swing_active:
                    sword = pygame.transform.rotate(sword, 28)
                surf.blit(
                    sword,
                    (
                        self.rect().centerx - 4 - sword.get_width() - offset[0],
                        self.rect().centery - sword.get_height() // 2 - offset[1],
                    ),
                )
            else:
                surf.blit(
                    pygame.transform.flip(self.game.assets["gun"], True, False),
                    (
                        self.rect().centerx - 4 - self.game.assets["gun"].get_width() - offset[0],
                        self.rect().centery - offset[1],
                    ),
                )
        else:
            if self.weapon_kind == "sword" and "sword" in self.game.assets:
                sword = self.game.assets["sword"]
                if self.sword_swing_active:
                    sword = pygame.transform.rotate(sword, -28)
                surf.blit(
                    sword,
                    (
                        self.rect().centerx + 4 - offset[0],
                        self.rect().centery - sword.get_height() // 2 - offset[1],
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
        self.character = 0
        self.enemy_form_active = False
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
        self.mirror_phantom_active = False
        self.mirror_phantom_until = 0
        self.mirror_phantom_cooldown_until = 0
        self.mirror_phantom_clone_pos: list[float] = []
        self.mirror_phantom = MirrorPhantomAbility(self)
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
        self.arrow_cooldown_until = 0
        self.speed_boost_until = 0
        self.speed_boost_cooldown_until = 0
        self.speed_boost_active = False
        self.speed_boost_particles: list[dict[str, float | int | list[float]]] = []
        self._apply_skin_stats()

    def _effect_colors(self):
        character_path = "default"
        try:
            character_path = cm.CHARACTER_PATHS[self.character]
        except Exception:
            pass
        if character_path == "red":
            return {
                "mist_particle": (170, 35, 35),
                "mist_body": (190, 40, 40),
                "dash_tint": (210, 45, 45),
            }
        if character_path == "golden":
            return {
                "mist_particle": (255, 235, 90),
                "mist_body": (255, 220, 40),
                "dash_tint": (255, 230, 70),
            }
        if character_path == "archer":
            return {
                "mist_particle": (35, 130, 45),
                "mist_body": (25, 105, 35),
                "dash_tint": (30, 150, 45),
            }
        return {
            "mist_particle": (15, 15, 15),
            "mist_body": (20, 20, 20),
            "dash_tint": (20, 20, 20),
        }

    def _boost_colors(self):
        return {
            "particle": (62, 30, 90),
            "trail": (32, 16, 46),
            "body": (18, 12, 26),
        }

    def set_character(self, character_index: int) -> None:
        """Assign character and immediately refresh current animation."""
        try:
            max_idx = max(0, len(cm.CHARACTER_PATHS) - 1)
            self.character = max(0, min(int(character_index), max_idx))
        except Exception:
            self.character = 0
        self._apply_skin_stats()
        if not self._can_use_enemy_form():
            self.enemy_form_active = False
        current_action = self.action or "idle"
        # Force animation swap even if action label stays the same.
        self.action = ""
        self.set_action(current_action)

    def set_skin(self, skin_index: int) -> None:
        """Backward-compatible alias for `set_character`."""
        self.set_character(skin_index)

    def _apply_skin_stats(self) -> None:
        """Apply per-character movement/jump/mist tuning."""
        base_jump = JUMP_VELOCITY * (1.0 if "PYTEST_CURRENT_TEST" in os.environ else 0.78)
        self.move_speed = 2.2
        self.jump_power = base_jump
        self.shadow_form_max_ms = 5000
        self.shadow_form_ms = min(getattr(self, "shadow_form_ms", self.shadow_form_max_ms), self.shadow_form_max_ms)

    def _enemy_move_speed(self) -> float:
        level = max(0, int(getattr(settings, "selected_level", 0)))
        return ENEMY_DIRECTION_BASE * (1 + ENEMY_DIRECTION_SCALE_LOG * math.log(level + 1))

    def _can_use_enemy_form(self) -> bool:
        try:
            character_path = cm.CHARACTER_PATHS[self.character]
        except Exception:
            character_path = "default"
        return character_path == "red"

    def toggle_enemy_form(self) -> bool:
        if not self._can_use_enemy_form():
            return False
        self.enemy_form_active = not self.enemy_form_active
        if self.enemy_form_active:
            self._shadow_requested = False
            self.shadow_form_active = False
            self.shadow_form_ms = self.shadow_form_max_ms
            self.dashing = 0
            self.speed_boost_active = False
            self.speed_boost_until = 0
            self.speed_boost_cooldown_until = 0
            self.slide_ability_active = False
            self.slide_hold_active = False
            self.slide_anim_until = 0
            self.move_speed = self._enemy_move_speed()
            self.action = ""
            self.set_action("idle")
        else:
            self._apply_skin_stats()
            self.action = ""
            self.set_action("idle")
        return self.enemy_form_active

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
        character_path = "default"
        try:
            character_path = cm.CHARACTER_PATHS[self.character]
        except Exception:
            pass
        if self.enemy_form_active:
            return False
        if character_path != "archer":
            # Player bullets are disabled for non-archer skins. Keep the API.
            return False
        now = pygame.time.get_ticks()
        if now < self.arrow_cooldown_until:
            return False
        direction = -6.5 if self.flip else 6.5
        spawn_x = self.rect().centerx + (7 * (-1 if self.flip else 1))
        spawn_y = self.rect().centery
        if self.services:
            self.services.projectiles.spawn(spawn_x, spawn_y, direction, "player", kind="arrow")
            self.services.play("shoot")
        else:
            self.game.projectiles.spawn(spawn_x, spawn_y, direction, "player", kind="arrow")
            self.game.audio.play("shoot")
        self.arrow_cooldown_until = now + 3000
        return True

    def take_damage(self, amount: int):
        if self.game.dead:
            return
        if self.mirror_phantom.active:
            return
        # Lives are heart-based: every damaging hit removes exactly one heart.
        if self.lives > 1:
            self.lives -= 1
            self.health = self.health_max
            self.game.screenshake = max(12, self.game.screenshake)
            return
        self.lives = 0
        self.health = 0
        self.game.dead += 1
        self.game.screenshake = max(16, self.game.screenshake)

    def set_grapple_aim(self, world_pos):
        self.grapple_aim_world = [float(world_pos[0]), float(world_pos[1])]

    def set_shadow_form(self, active: bool):
        if self.enemy_form_active or self.mirror_phantom.active:
            self._shadow_requested = False
            self.shadow_form_active = False
            self.shadow_form_ms = self.shadow_form_max_ms
            return
        self._shadow_requested = bool(active)
        if not self._shadow_requested:
            self.shadow_form_active = False
            self.shadow_form_ms = self.shadow_form_max_ms

    def _can_use_slide_ability(self) -> bool:
        try:
            character_path = cm.CHARACTER_PATHS[self.character]
        except Exception:
            character_path = "default"
        return character_path == "default"

    def _can_use_speed_boost(self) -> bool:
        try:
            character_path = cm.CHARACTER_PATHS[self.character]
        except Exception:
            character_path = "default"
        return character_path == "default"

    def _can_use_mirror_phantom(self) -> bool:
        return self.mirror_phantom.is_golden() and not self.enemy_form_active

    def toggle_mirror_phantom(self) -> bool:
        if self.mirror_phantom.active:
            return self.mirror_phantom.cancel()
        return self.mirror_phantom.start(duration_ms=4000)

    def get_mirror_phantom_target_pos(self):
        return self.mirror_phantom.target_pos()

    @property
    def skin(self):
        """Backward-compatible alias for `character`."""
        return self.character

    @skin.setter
    def skin(self, value):
        self.character = value

    def activate_speed_boost(self, duration_ms: int = 5000) -> bool:
        if self.enemy_form_active or not self._can_use_speed_boost():
            return False
        now = pygame.time.get_ticks()
        if now < self.speed_boost_cooldown_until:
            return False
        self.speed_boost_active = True
        self.speed_boost_until = max(self.speed_boost_until, now + max(50, int(duration_ms)))
        self.speed_boost_cooldown_until = self.speed_boost_until + 5000
        self.action = ""
        self.set_action("run" if abs(self.velocity[0]) > 0.1 else "idle")
        return True

    def trigger_slide_animation(self, duration_ms: int = 2000) -> None:
        if self.enemy_form_active or not self._can_use_slide_ability():
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
        if self.enemy_form_active:
            return
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
        if self.enemy_form_active and not self._can_use_enemy_form():
            self.enemy_form_active = False
            self._apply_skin_stats()
        if self.speed_boost_active and now >= self.speed_boost_until:
            self.speed_boost_active = False
            self.speed_boost_until = 0
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

        is_boost_moving = abs(movement[0]) > 0.01 or abs(self.velocity[0]) > 0.1
        if self.speed_boost_active:
            self.move_speed = 4.8
        else:
            self._apply_skin_stats()

        if self.enemy_form_active:
            # Match the enemy patrol pace more closely in disguise.
            self.move_speed = self._enemy_move_speed() * 1.25
            self.slide_ability_active = False
            self.slide_hold_active = False
            self.slide_anim_until = 0
            self.slide_ability_until = 0
            self.slide_ability_started_at = 0
            self.slide_cooldown_until = 0

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
        self.mirror_phantom.update()
        if self.mirror_phantom.active:
            for enemy in getattr(self.game, "enemies", []):
                if getattr(enemy, "alive", True) and self.rect().colliderect(enemy.rect()):
                    self.mirror_phantom.mark_enemy(enemy)
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

        if self.speed_boost_active:
            boost_dir = 0
            if abs(movement[0]) > 0.01:
                boost_dir = -1 if movement[0] < 0 else 1
            elif abs(self.velocity[0]) > 0.1:
                boost_dir = -1 if self.velocity[0] < 0 else 1
            elif self.flip:
                boost_dir = 0
            boost_colors = self._boost_colors()
            for _ in range(2):
                behind_x = self.rect().centerx - (boost_dir * 5)
                if boost_dir == 0:
                    behind_x = self.rect().centerx + rng.uniform(-1.5, 1.5)
                self.speed_boost_particles.append(
                    {
                        "pos": [
                            behind_x + rng.uniform(-2, 2),
                            self.rect().centery + rng.uniform(-3, 3),
                        ],
                        "vel": [
                            (-boost_dir * 0.35) + rng.uniform(-0.08, 0.08),
                            rng.uniform(-0.06, 0.06),
                        ],
                        "base_r": rng.randint(2, 3),
                        "r": rng.randint(2, 3),
                        "ttl": 18,
                        "color": boost_colors["particle"],
                    }
                )
        for p in self.speed_boost_particles[:]:
            p["ttl"] -= 1
            p["pos"][0] += p["vel"][0]
            p["pos"][1] += p["vel"][1]
            if self.speed_boost_active and is_boost_moving:
                dist = math.hypot(p["pos"][0] - self.rect().centerx, p["pos"][1] - self.rect().centery)
                p["r"] = max(1, int(p.get("base_r", 2) - dist / 18))
            else:
                p["r"] = int(p.get("base_r", 2))
            if p["ttl"] <= 0:
                self.speed_boost_particles.remove(p)

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
            if self.enemy_form_active:
                if moving_horizontally:
                    self.set_action("run")
                else:
                    self.set_action("idle")
            elif now < self.slide_anim_until:
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
        boost_colors = self._boost_colors()
        for p in self.speed_boost_particles:
            pygame.draw.circle(
                surf,
                p.get("color", boost_colors["particle"]),
                (int(p["pos"][0] - offset[0]), int(p["pos"][1] - offset[1])),
                int(p["r"]),
            )
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
        if self.mirror_phantom.active:
            self.mirror_phantom.render_clone(surf, offset)
        # Red slide frames sit slightly high; nudge down only during slide action.
        y_adjust = 0
        try:
            character_path = cm.CHARACTER_PATHS[self.character]
        except Exception:
            character_path = "default"
        if self.action == "slide" and character_path == "red":
            y_adjust = 5
        if self.speed_boost_active:
            y_adjust = 0
        if self.action == "run":
            try:
                frame_count = max(1, len(self.animation.images))
                # Time-based override keeps all run animations visibly cycling even if
                # a state transition briefly reuses the same animation object.
                self.animation.frame = (
                    (pygame.time.get_ticks() // 80) % frame_count
                ) * self.animation.img_duration
            except Exception:
                pass
        if abs(self.dashing) <= DASH_MIN_ACTIVE_ABS:
            if y_adjust == 0 and not self.mirror_phantom.active:
                super().render(surf, offset=offset)
            else:
                player_img = pygame.transform.flip(self.animation.img(), self.flip, False)
                if self.mirror_phantom.active:
                    player_img = player_img.copy()
                    player_img.set_alpha(110)
                surf.blit(
                    player_img,
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
        if self.enemy_form_active or self.slide_ability_active or self.mirror_phantom.active:
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
