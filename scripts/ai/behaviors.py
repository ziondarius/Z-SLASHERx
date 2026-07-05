import math
from typing import Any, Dict

import pygame

from scripts.ai.core import Policy
from scripts.constants import (
    ENEMY_DIRECTION_BASE,
    ENEMY_DIRECTION_SCALE_LOG,
    ENEMY_ALERT_DISTANCE_MAX,
    ENEMY_ALERT_DISTANCE_MIN,
    ENEMY_ALERT_LOST_MS,
    ENEMY_ALERT_SHOOT_COOLDOWN,
    ENEMY_ALERT_SPEED_MULT,
    ENEMY_ALERT_VERTICAL_TOLERANCE,
)
from scripts.rng_service import RNGService
from scripts.settings import settings


class ScriptedEnemyPolicy(Policy):
    """Patrol platform edges/walls and shoot only when facing the player."""

    def decide(self, entity: Any, context: Any) -> Dict[str, Any]:
        game = entity.game
        result = {"movement": (0, 0), "shoot": False, "shoot_direction": 0}
        tilemap = game.tilemap

        if self._update_alert_state(entity, game, result):
            return result

        self._patrol(entity, tilemap, result)
        self._check_shoot(entity, game, result)
        return result

    def _walk_speed(self) -> float:
        return ENEMY_DIRECTION_BASE * (1 + ENEMY_DIRECTION_SCALE_LOG * math.log(settings.selected_level + 1))

    def _patrol(self, entity, tilemap, result):
        check_x = entity.rect().centerx + (-7 if entity.flip else 7)
        check_y = entity.rect().bottom + 2

        # Turn around at walls or platform edges; otherwise keep walking.
        if tilemap.solid_check((check_x, check_y)):
            if entity.collisions["right"] or entity.collisions["left"]:
                entity.flip = not entity.flip
            else:
                direction = self._walk_speed()
                move_x = -direction if entity.flip else direction
                result["movement"] = (move_x, 0)
        else:
            entity.flip = not entity.flip

    def _has_line_of_sight(self, entity, game) -> bool:
        player = game.player
        tilemap = game.tilemap
        ex = float(entity.rect().centerx)
        ey = float(entity.rect().centery)
        px = float(player.rect().centerx)
        py = float(player.rect().centery)

        if abs(py - ey) > ENEMY_ALERT_VERTICAL_TOLERANCE:
            return False

        step = 4 if px >= ex else -4
        x = ex
        while (step > 0 and x < px) or (step < 0 and x > px):
            if tilemap.solid_check((x, ey)):
                return False
            x += step
        return True

    def _ground_ahead(self, entity, tilemap, direction: int, look_ahead_px: float = 0.0) -> bool:
        lead_x = entity.rect().centerx + (entity.rect().width // 2 - 1) * direction + (look_ahead_px * direction)
        foot_y = entity.rect().bottom + 2
        probe_a = (lead_x, foot_y)
        probe_b = (lead_x - (2 * direction), foot_y)
        return bool(tilemap.solid_check(probe_a) or tilemap.solid_check(probe_b))

    def _update_alert_state(self, entity, game, result) -> bool:
        now = pygame.time.get_ticks()
        player = game.player
        player_dx = float(player.rect().centerx - entity.rect().centerx)
        player_dy = float(player.rect().centery - entity.rect().centery)

        alert_active = bool(getattr(entity, "enemy_alert_active", False))
        lost_since = int(getattr(entity, "enemy_alert_lost_since", 0))
        state = getattr(entity, "enemy_alert_state", "idle")

        facing_player = (entity.flip and player_dx < 0) or (not entity.flip and player_dx > 0)
        los = self._has_line_of_sight(entity, game)

        if not alert_active:
            if facing_player and los:
                alert_active = True
                state = "alert"
                lost_since = 0
                setattr(entity, "enemy_alert_active", True)
                setattr(entity, "enemy_alert_state", state)
                setattr(entity, "enemy_alert_lost_since", lost_since)
                setattr(entity, "enemy_alert_icon", "exclamation_mark")
                setattr(entity, "shoot_cooldown_enemy", 0)
            else:
                setattr(entity, "enemy_alert_active", False)
                setattr(entity, "enemy_alert_state", "idle")
                setattr(entity, "enemy_alert_lost_since", 0)
                setattr(entity, "enemy_alert_icon", None)
                return False

        # Alert behavior.
        if alert_active:
            if los:
                state = "alert"
                lost_since = 0
                setattr(entity, "enemy_alert_icon", "exclamation_mark")
                setattr(entity, "enemy_alert_lost_since", 0)
            else:
                if not lost_since:
                    lost_since = now
                setattr(entity, "enemy_alert_state", "lost")
                setattr(entity, "enemy_alert_lost_since", lost_since)
                setattr(entity, "enemy_alert_icon", "question_mark")
                result["movement"] = (0, 0)
                result["shoot"] = False
                if now - lost_since >= ENEMY_ALERT_LOST_MS:
                    setattr(entity, "enemy_alert_active", False)
                    setattr(entity, "enemy_alert_state", "idle")
                    setattr(entity, "enemy_alert_lost_since", 0)
                    setattr(entity, "enemy_alert_icon", None)
                return True

            # Keep facing the player once alerted.
            if player_dx > 0:
                entity.flip = False
            elif player_dx < 0:
                entity.flip = True

            distance = abs(player_dx)
            move_x = 0.0
            alert_speed = self._walk_speed() * ENEMY_ALERT_SPEED_MULT
            if distance < ENEMY_ALERT_DISTANCE_MIN:
                move_dir = -1 if player_dx > 0 else 1
                if self._ground_ahead(entity, game.tilemap, move_dir, look_ahead_px=8):
                    move_x = alert_speed * move_dir
            elif distance > ENEMY_ALERT_DISTANCE_MAX:
                move_dir = 1 if player_dx > 0 else -1
                if self._ground_ahead(entity, game.tilemap, move_dir, look_ahead_px=8):
                    move_x = alert_speed * move_dir

            if move_x != 0:
                result["movement"] = (move_x, 0)
            else:
                # If the platform is too tight to maintain the preferred range,
                # hold position and simply keep facing the player.
                if player_dx > 0:
                    entity.flip = False
                elif player_dx < 0:
                    entity.flip = True
                result["movement"] = (0, 0)

            if abs(player_dy) <= ENEMY_ALERT_VERTICAL_TOLERANCE and ENEMY_ALERT_DISTANCE_MIN <= distance <= ENEMY_ALERT_DISTANCE_MAX:
                self._check_shoot(entity, game, result)
            else:
                result["shoot"] = False
            return True

        return False

    def _check_shoot(self, entity, game, result):
        cooldown = int(getattr(entity, "shoot_cooldown_enemy", 0))
        if cooldown > 0:
            setattr(entity, "shoot_cooldown_enemy", cooldown - 1)
            return
        dis = (
            game.player.rect().centerx - entity.rect().centerx,
            game.player.rect().centery - entity.rect().centery,
        )
        if abs(dis[1]) < ENEMY_ALERT_VERTICAL_TOLERANCE:
            if entity.flip and dis[0] < 0:  # Facing left, player to left
                result["shoot"] = True
                result["shoot_direction"] = -1
                setattr(entity, "shoot_cooldown_enemy", ENEMY_ALERT_SHOOT_COOLDOWN)
            if not entity.flip and dis[0] > 0:  # Facing right, player to right
                result["shoot"] = True
                result["shoot_direction"] = 1
                setattr(entity, "shoot_cooldown_enemy", ENEMY_ALERT_SHOOT_COOLDOWN)


class PatrolPolicy(Policy):
    """Walks back and forth continuously without shooting."""

    def decide(self, entity: Any, context: Any) -> Dict[str, Any]:
        game = entity.game
        tilemap = game.tilemap
        result = {"movement": (0, 0), "shoot": False}

        # Ensure walking timer is active or just ignore it and force move?
        # To keep consistent with physics/anim, we command movement.

        check_x = entity.rect().centerx + (-7 if entity.flip else 7)
        check_y = entity.pos[1] + 23

        if tilemap.solid_check((check_x, check_y)):
            if entity.collisions["right"] or entity.collisions["left"]:
                entity.flip = not entity.flip
            else:
                direction = ENEMY_DIRECTION_BASE * (
                    1 + ENEMY_DIRECTION_SCALE_LOG * math.log(settings.selected_level + 1)
                )
                move_x = -direction if entity.flip else direction
                result["movement"] = (move_x, 0)
        else:
            entity.flip = not entity.flip

        return result


class ShooterPolicy(Policy):
    """Stationary turret that tracks and shoots at the player."""

    def decide(self, entity: Any, context: Any) -> Dict[str, Any]:
        game = entity.game
        rng = RNGService.get()
        result = {"movement": (0, 0), "shoot": False, "shoot_direction": 0}

        # Always face player
        diff_x = game.player.pos[0] - entity.pos[0]
        if diff_x > 0:
            entity.flip = False
        else:
            entity.flip = True

        # Shoot check
        # Simple cooldown implemented via RNG for now (or could use entity state)
        if rng.random() < 0.02:  # 2% chance per frame ~ 1 shot per second at 60fps
            dis = (
                game.player.pos[0] - entity.pos[0],
                game.player.pos[1] - entity.pos[1],
            )
            # Range check
            if abs(dis[0]) < 200 and abs(dis[1]) < 30:
                result["shoot"] = True
                result["shoot_direction"] = 1 if diff_x > 0 else -1

        return result


class ChaserPolicy(Policy):
    """Actively pathfinds/moves towards player within range."""

    def decide(self, entity: Any, context: Any) -> Dict[str, Any]:
        game = entity.game
        tilemap = game.tilemap
        rng = RNGService.get()
        result = {"movement": (0, 0), "shoot": False, "shoot_direction": 0, "jump": False}

        # Calculate vector to player
        dis_x = game.player.pos[0] - entity.pos[0]
        dis_y = game.player.pos[1] - entity.pos[1]
        dist_sq = dis_x * dis_x + dis_y * dis_y

        # Engage if within ~300px
        if dist_sq < 90000:
            direction = ENEMY_DIRECTION_BASE * (1 + ENEMY_DIRECTION_SCALE_LOG * math.log(settings.selected_level + 1))
            # Basic horizontal seek
            if dis_x > 10:
                entity.flip = False
                move_x = direction
            elif dis_x < -10:
                entity.flip = True
                move_x = -direction
            else:
                move_x = 0

            # Wall/Cliff logic: Jump if blocked, or stop?
            check_x = entity.rect().centerx + (10 if move_x > 0 else -10)
            check_y = entity.pos[1] + 23

            blocked = False
            # Check wall
            if move_x != 0:
                if tilemap.solid_check((check_x, entity.rect().centery)):
                    blocked = True
                if entity.collisions["right"] or entity.collisions["left"]:
                    blocked = True

            # Check cliff
            if not tilemap.solid_check((check_x, check_y)):
                # If cliff, maybe jump if player is above/across?
                # For now, just stop to avoid suicide
                blocked = True
                # Unless we can jump?
                if dis_y < -20:  # Player is above
                    result["jump"] = True

            if not blocked or result["jump"]:
                result["movement"] = (move_x, 0)

            # Jump if player is significantly above
            if dis_y < -40 and rng.random() < 0.02 and entity.collisions["down"]:
                result["jump"] = True

            # Shoot check
            if abs(dis_y) < 30 and abs(dis_x) < 150:
                if rng.random() < 0.05:
                    result["shoot"] = True
                    result["shoot_direction"] = 1 if dis_x > 0 else -1

        return result


class JumperPolicy(Policy):
    """Moves horizontally and jumps frequently to be hard to hit."""

    def decide(self, entity: Any, context: Any) -> Dict[str, Any]:
        game = entity.game
        tilemap = game.tilemap
        rng = RNGService.get()
        result = {"movement": (0, 0), "shoot": False, "shoot_direction": 0, "jump": False}

        # Patrol logic base
        check_x = entity.rect().centerx + (-7 if entity.flip else 7)
        check_y = entity.pos[1] + 23

        should_turn = False
        if tilemap.solid_check((check_x, check_y)):
            if entity.collisions["right"] or entity.collisions["left"]:
                should_turn = True
            else:
                direction = ENEMY_DIRECTION_BASE * (
                    1 + ENEMY_DIRECTION_SCALE_LOG * math.log(settings.selected_level + 1)
                )
                move_x = -direction if entity.flip else direction
                result["movement"] = (move_x, 0)
        else:
            should_turn = True

        if should_turn:
            # Chance to jump over obstacle instead of turning?
            if rng.random() < 0.5:
                result["jump"] = True
                # Maintain momentum
                move_x = -ENEMY_DIRECTION_BASE if entity.flip else ENEMY_DIRECTION_BASE
                result["movement"] = (move_x, 0)
            else:
                entity.flip = not entity.flip

        # Random jumps
        if rng.random() < 0.02 and entity.collisions["down"]:
            result["jump"] = True

        # Shoot if player aligned
        dis_y = game.player.pos[1] - entity.pos[1]
        if abs(dis_y) < 20 and rng.random() < 0.01:
            result["shoot"] = True
            result["shoot_direction"] = -1 if entity.flip else 1

        return result
