from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import pygame

from scripts.particle import Particle
from scripts.rng_service import RNGService


@dataclass
class MirrorPhantomAbility:
    """Golden ninja Mirror Phantom state.

    Owns the mirrored target, active timer, and marked-enemy bookkeeping.
    Player methods call into this helper so the ability logic stays isolated
    from input and rendering plumbing.
    """

    player: object
    active: bool = False
    until_ms: int = 0
    cooldown_until_ms: int = 0
    cooldown_ms: int = 8000
    clone_pos: list[float] = field(default_factory=list)
    marked_enemy_ids: set[int] = field(default_factory=set)
    clone_flip: bool = False
    def is_golden(self) -> bool:
        try:
            from scripts.collectableManager import CollectableManager as cm

            return cm.CHARACTER_PATHS[getattr(self.player, "character", 0)] == "golden"
        except Exception:
            return False

    def can_activate(self) -> bool:
        now = pygame.time.get_ticks()
        return (
            self.is_golden()
            and not getattr(self.player, "enemy_form_active", False)
            and now >= self.cooldown_until_ms
        )

    def start(self, duration_ms: int = 4000) -> bool:
        if not self.can_activate():
            return False
        now = pygame.time.get_ticks()
        self.active = True
        self.until_ms = now + max(100, int(duration_ms))
        self.clone_pos = list(getattr(self.player, "pos", [0, 0]))
        self.clone_flip = bool(getattr(self.player, "flip", False))
        self.marked_enemy_ids.clear()
        setattr(self.player, "mirror_phantom_active", True)
        setattr(self.player, "mirror_phantom_until", self.until_ms)
        setattr(self.player, "mirror_phantom_clone_pos", list(self.clone_pos))
        return True

    def cancel(self) -> bool:
        if not self.active:
            return False
        self._shatter()
        return True

    def update(self) -> None:
        if not self.active:
            return
        now = pygame.time.get_ticks()
        if now >= self.until_ms:
            self._shatter()
            return
        setattr(self.player, "mirror_phantom_until", self.until_ms)
        setattr(self.player, "mirror_phantom_active", True)
        setattr(self.player, "mirror_phantom_clone_pos", list(self.clone_pos))

    def target_pos(self):
        if self.active and self.clone_pos:
            return list(self.clone_pos)
        return list(getattr(self.player, "pos", [0, 0]))

    def mark_enemy(self, enemy) -> bool:
        if not self.active:
            return False
        enemy_id = id(enemy)
        if enemy_id in self.marked_enemy_ids:
            return False
        self.marked_enemy_ids.add(enemy_id)
        setattr(enemy, "golden_marked", True)
        setattr(enemy, "golden_marked_id", enemy_id)
        return True

    def _spawn_gold_explosion(self, origin):
        game = getattr(self.player, "game", None)
        if not game:
            return
        rng = RNGService.get()
        for _ in range(28):
            ang = rng.random() * math.tau
            speed = rng.random() * 1.8 + 0.6
            vel = [math.cos(ang) * speed, math.sin(ang) * speed]
            tint = (255, 215, 80) if rng.random() > 0.45 else (120, 90, 20)
            game.particles.append(
                Particle(
                    game,
                    "particle",
                    origin,
                    velocity=vel,
                    frame=rng.randint(0, 7),
                    tint=tint,
                )
            )

    def _damage_marked_enemies(self):
        game = getattr(self.player, "game", None)
        if not game:
            return
        for enemy in list(getattr(game, "enemies", [])):
            if id(enemy) not in self.marked_enemy_ids:
                continue
            if hasattr(enemy, "take_damage"):
                enemy.take_damage(999)
            else:
                enemy.alive = False
            setattr(enemy, "golden_marked", False)
            if hasattr(game, "enemies") and enemy in game.enemies:
                try:
                    game.enemies.remove(enemy)
                except ValueError:
                    pass

    def _shatter(self) -> None:
        origin = list(self.clone_pos or getattr(self.player, "pos", [0, 0]))
        self._spawn_gold_explosion(origin)
        self._damage_marked_enemies()
        now = pygame.time.get_ticks()
        try:
            self.player.pos = list(self.clone_pos or getattr(self.player, "pos", [0, 0]))
            self.player.velocity = [0, 0]
            self.player.last_movement = [0, 0]
            self.player.flip = self.clone_flip
        except Exception:
            pass
        self.active = False
        self.until_ms = 0
        self.cooldown_until_ms = now + self.cooldown_ms
        self.clone_pos = []
        self.marked_enemy_ids.clear()
        self.clone_flip = False
        setattr(self.player, "mirror_phantom_active", False)
        setattr(self.player, "mirror_phantom_until", 0)
        setattr(self.player, "mirror_phantom_clone_pos", [])
        setattr(self.player, "mirror_phantom_cooldown_until", self.cooldown_until_ms)

    def render_clone(self, surf, offset=(0, 0)):
        if not self.active or not self.clone_pos:
            return
        player = self.player
        try:
            from scripts.collectableManager import CollectableManager as cm

            character_path = cm.CHARACTER_PATHS[getattr(player, "character", 0)]
            idle_anim = player.game.assets[f"player/{character_path}/idle"]
            img = pygame.transform.flip(idle_anim.img(), self.clone_flip, False)
        except Exception:
            try:
                img = pygame.transform.flip(player.animation.img(), self.clone_flip, False)
            except Exception:
                return
        surf.blit(
            img,
            (
                self.clone_pos[0] - offset[0] + player.anim_offset[0],
                self.clone_pos[1] - offset[1] + player.anim_offset[1],
            ),
        )

    def apply_mark_visual(self, surf, enemy, offset=(0, 0)):
        if not getattr(enemy, "golden_marked", False):
            return
        r = enemy.rect()
        left = int(r.left - offset[0])
        top = int(r.top - offset[1])
        right = int(r.right - offset[0])
        bottom = int(r.bottom - offset[1])
        pygame.draw.line(surf, (255, 210, 70), (left, top), (right, bottom), 2)
        pygame.draw.line(surf, (255, 210, 70), (left, bottom), (right, top), 2)
