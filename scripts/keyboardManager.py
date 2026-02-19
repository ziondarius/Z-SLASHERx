import sys

import pygame


class KeyboardManager:
    def __init__(self, game):
        self.game = game
        self._dash_was_down = False
        self._dash_down_since = 0
        self._shadow_started_from_hold = False

    # New centralized event processing (Issue 10 migration support)
    def process_events(self, events):
        """Process a batch of pygame events.

        This mirrors legacy logic in handle_keyboard_input but without
        polling the global event queue. It enables a single global
        event fetch in the application loop / StateManager.
        """
        for event in events:
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    # GameState will push a PauseState; keep legacy flag for now
                    self.game.paused = True
                if event.key == pygame.K_a:
                    self.game.movement[0] = True
                if event.key == pygame.K_d:
                    self.game.movement[1] = True
                if event.key == pygame.K_w:
                    if self.game.player.jump():
                        self.game.audio.play("jump")
                if event.key == pygame.K_LEFT:
                    self.game.movement[0] = True
                if event.key == pygame.K_RIGHT:
                    self.game.movement[1] = True
                if event.key == pygame.K_UP:
                    if self.game.player.jump():
                        self.game.audio.play("jump")
                if event.key == pygame.K_x:
                    self.game.player.shoot()
                if event.key == pygame.K_r:
                    self.game.dead += 1
                    self.game.player.lives -= 1
                if event.key == pygame.K_p:
                    if self.game.saves > 0:
                        self.game.saves -= 1
                        self.game.player.respawn_pos = list(self.game.player.pos)
            if event.type == pygame.KEYUP:
                if event.key in (pygame.K_a, pygame.K_LEFT):
                    self.game.movement[0] = False
                if event.key in (pygame.K_d, pygame.K_RIGHT):
                    self.game.movement[1] = False

    def handle_keyboard_input(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            # Movement keys
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.game.paused = True

                # W, A, S, D
                if event.key == pygame.K_a:
                    self.game.movement[0] = True
                if event.key == pygame.K_d:
                    self.game.movement[1] = True
                if event.key == pygame.K_w:
                    if self.game.player.jump():
                        self.game.audio.play("jump")

                # Arrow keys
                if event.key == pygame.K_LEFT:
                    self.game.movement[0] = True
                if event.key == pygame.K_RIGHT:
                    self.game.movement[1] = True
                if event.key == pygame.K_UP:
                    if self.game.player.jump():
                        self.game.audio.play("jump")

                # X for shooting
                if event.key == pygame.K_x:
                    self.game.player.shoot()

                # Respawn
                if event.key == pygame.K_r:
                    self.game.dead += 1
                    self.game.player.lives -= 1
                    print(self.game.dead)

                # Save position
                if event.key == pygame.K_p:
                    if self.game.saves > 0:
                        self.game.saves -= 1
                        self.game.player.respawn_pos = list(self.game.player.pos)
                        print("saved respawn pos: ", self.game.player.respawn_pos)

            # Stop movement
            if event.type == pygame.KEYUP:
                if event.key == pygame.K_a:
                    self.game.movement[0] = False
                if event.key == pygame.K_d:
                    self.game.movement[1] = False

                if event.key == pygame.K_LEFT:
                    self.game.movement[0] = False
                if event.key == pygame.K_RIGHT:
                    self.game.movement[1] = False

    def handle_mouse_input(self):
        if not hasattr(self.game, "player") or self.game.player is None:
            return

        # Convert screen mouse coordinates to world coordinates.
        mx, my = pygame.mouse.get_pos()
        try:
            sx = mx * self.game.BASE_W / max(1, self.game.WIN_W)
            sy = my * self.game.BASE_H / max(1, self.game.WIN_H)
        except Exception:
            sx, sy = mx, my
        world_pos = (sx + self.game.scroll[0], sy + self.game.scroll[1])
        if hasattr(self.game.player, "set_grapple_aim"):
            self.game.player.set_grapple_aim(world_pos)

        mouse_buttons = pygame.mouse.get_pressed()
        left = bool(mouse_buttons[0])

        if left:  # Left mouse button
            self.game.player.shoot()

        # Dash / Black Mist on Space:
        # - Tap Space: dash immediately
        # - Hold Space >= 2s: enable black mist while held
        keys = pygame.key.get_pressed()
        dash_held = bool(keys[pygame.K_SPACE])
        now = pygame.time.get_ticks()
        if dash_held and not self._dash_was_down:
            self.game.player.dash()
            self._dash_down_since = now
            self._shadow_started_from_hold = False
        if dash_held and self._dash_was_down and not self._shadow_started_from_hold:
            if now - self._dash_down_since >= 2000 and hasattr(self.game.player, "set_shadow_form"):
                self.game.player.set_shadow_form(True)
                self._shadow_started_from_hold = True
        if (not dash_held) and self._dash_was_down:
            if hasattr(self.game.player, "set_shadow_form"):
                self.game.player.set_shadow_form(False)
            self._shadow_started_from_hold = False
        self._dash_was_down = dash_held
