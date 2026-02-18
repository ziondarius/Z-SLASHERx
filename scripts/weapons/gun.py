from __future__ import annotations

from scripts.collectableManager import CollectableManager as cm
from scripts.constants import PROJECTILE_SPEED
from scripts.settings import settings

from .base import FireResult, Weapon


class GunWeapon(Weapon):
    name = "gun"

    def can_fire(self, player) -> bool:
        try:
            selected_name = cm.WEAPONS[settings.selected_weapon]
        except Exception:  # pragma: no cover - defensive
            return False
        if selected_name == "Default":
            return False
        return player.game.cm.get_amount(selected_name) > 0 and player.shoot_cooldown == 0

    def fire(self, player):
        if not self.can_fire(player):
            return None
        if player.services:
            player.services.play("shoot")
        else:
            player.game.audio.play("shoot")
        direction = -PROJECTILE_SPEED if player.flip else PROJECTILE_SPEED
        (player.services.projectiles.spawn if player.services else player.game.projectiles.spawn)(
            player.rect().centerx + (7 * (-1 if player.flip else 1)),
            player.rect().centery,
            direction,
            "player",
        )
        # Infinite ammo: do not consume ammo on shot.
        player.shoot_cooldown = 10
        return FireResult(spawned=True, ammo_used=0)
