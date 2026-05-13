import sys

import pygame


class KeyboardManager:
    def __init__(self, game):
        self.game = game
        self._dash_was_down = False
        self._space_was_down = False
        self._hover_was_down = False
        self._dash_down_since = 0
        self._shadow_started_from_hold = False
        self._kbd_left = False
        self._kbd_right = False
        self._axis_deadzone = 0.35
        self._trigger_deadzone = 0.35
        # Controller jump on A only.
        self._jump_buttons = {0}
        # Controller slide ability on B only.
        self._ability_buttons = {1}
        # Controller Y behaves like keyboard X action (shoot).
        self._shoot_buttons = {3}
        # Hover hold buttons (LB / RB), broad but isolated from dash inputs.
        self._hover_buttons = {4, 5, 6, 9, 10, 13, 14}
        # Dash trigger buttons (LT / RT) when exposed as digital buttons.
        self._dash_trigger_buttons = {7, 8}
        self._trigger_axis_pressed = {}
        # SDL controller-button state (for devices reporting CONTROLLERBUTTON* events).
        self._ctrl_lb_down = False
        self._ctrl_rb_down = False
        self._ability_was_down = False
        self._kbd_slide_held = False
        self._joystick = None
        try:
            pygame.joystick.init()
            if pygame.joystick.get_count() > 0:
                self._joystick = pygame.joystick.Joystick(0)
                self._joystick.init()
        except Exception:
            self._joystick = None

    def _read_stick_horizontal(self):
        if self._joystick is None:
            return False, False
        try:
            x = float(self._joystick.get_axis(0))
        except Exception:
            return False, False
        return (x <= -self._axis_deadzone, x >= self._axis_deadzone)

    def _apply_horizontal_movement(self):
        stick_left, stick_right = self._read_stick_horizontal()
        self.game.movement[0] = bool(self._kbd_left or stick_left)
        self.game.movement[1] = bool(self._kbd_right or stick_right)

    def _controller_trigger_held(self):
        if self._joystick is None:
            return False
        # Prefer analog trigger axes when available.
        try:
            axis_count = self._joystick.get_numaxes()
        except Exception:
            axis_count = 0
        if axis_count > 0:
            for axis_idx in (4, 5, 2, 3):
                if axis_idx >= axis_count:
                    continue
                try:
                    v = float(self._joystick.get_axis(axis_idx))
                except Exception:
                    continue
                # Normalize trigger value robustly across controller drivers:
                # - If resting near -1, treat range as [-1, 1] and map to [0, 1]
                # - Otherwise assume [0, 1]
                if v < -0.1:
                    norm = (v + 1.0) * 0.5
                else:
                    norm = v
                if norm >= self._trigger_deadzone:
                    return True
        return False

    def _controller_hover_held(self):
        if self._ctrl_lb_down or self._ctrl_rb_down:
            return True
        if self._joystick is None:
            return False
        try:
            btn_count = self._joystick.get_numbuttons()
        except Exception:
            return False
        for b in self._hover_buttons:
            if b < btn_count and self._joystick.get_button(b):
                return True
        return False

    def _controller_ability_held(self):
        if self._joystick is None:
            return False
        try:
            btn_count = self._joystick.get_numbuttons()
        except Exception:
            return False
        for b in self._ability_buttons:
            if b < btn_count and self._joystick.get_button(b):
                return True
        return False

    def _movement_dir(self) -> float:
        # Positive => right, negative => left
        dir_val = float(self.game.movement[1]) - float(self.game.movement[0])
        if abs(dir_val) > 0.01:
            return dir_val
        if self._joystick is not None:
            try:
                x = float(self._joystick.get_axis(0))
                if abs(x) >= self._axis_deadzone:
                    return x
            except Exception:
                pass
        return 0.0

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
                if getattr(self.game.player, "slide_ability_active", False):
                    continue
                if event.key == pygame.K_ESCAPE:
                    # GameState will push a PauseState; keep legacy flag for now
                    self.game.paused = True
                if event.key == pygame.K_a:
                    self._kbd_left = True
                    self._apply_horizontal_movement()
                if event.key == pygame.K_d:
                    self._kbd_right = True
                    self._apply_horizontal_movement()
                if event.key == pygame.K_w:
                    if self.game.player.jump():
                        self.game.audio.play("jump")
                if event.key == pygame.K_LEFT:
                    self._kbd_left = True
                    self._apply_horizontal_movement()
                if event.key == pygame.K_RIGHT:
                    self._kbd_right = True
                    self._apply_horizontal_movement()
                if event.key == pygame.K_UP:
                    if self.game.player.jump():
                        self.game.audio.play("jump")
                if event.key == pygame.K_x:
                    self.game.player.shoot()
                if event.key in (pygame.K_s, pygame.K_DOWN):
                    self._kbd_slide_held = True
                if event.key == pygame.K_r:
                    self.game.dead += 1
                    self.game.player.lives -= 1
                if event.key == pygame.K_p:
                    if self.game.saves > 0:
                        self.game.saves -= 1
                        self.game.player.respawn_pos = list(self.game.player.pos)
            if event.type == pygame.KEYUP:
                if event.key in (pygame.K_a, pygame.K_LEFT):
                    self._kbd_left = False
                    self._apply_horizontal_movement()
                if event.key in (pygame.K_d, pygame.K_RIGHT):
                    self._kbd_right = False
                    self._apply_horizontal_movement()
                if event.key in (pygame.K_s, pygame.K_DOWN):
                    self._kbd_slide_held = False
            if event.type == pygame.JOYBUTTONDOWN:
                if getattr(self.game.player, "slide_ability_active", False):
                    continue
                if getattr(event, "button", None) in self._jump_buttons:
                    if self.game.player.jump():
                        self.game.audio.play("jump")
                if getattr(event, "button", None) in self._dash_trigger_buttons:
                    self.game.player.dash()
                if getattr(event, "button", None) in self._shoot_buttons:
                    self.game.player.shoot()
                if getattr(event, "button", None) in self._ability_buttons:
                    self.game.player.trigger_slide_animation()
            if event.type == pygame.CONTROLLERBUTTONDOWN:
                btn = getattr(event, "button", None)
                # SDL gamecontroller mapping fallback for ability button.
                if btn in self._ability_buttons:
                    self.game.player.trigger_slide_animation()
                if btn == 9:
                    self._ctrl_lb_down = True
                if btn == 10:
                    self._ctrl_rb_down = True
            if event.type == pygame.CONTROLLERBUTTONUP:
                btn = getattr(event, "button", None)
                if btn == 9:
                    self._ctrl_lb_down = False
                if btn == 10:
                    self._ctrl_rb_down = False
            if event.type == pygame.JOYAXISMOTION:
                # Trigger axes: dash on rising edge only (controller dash only, no hover).
                axis = getattr(event, "axis", None)
                if axis in (2, 3, 4, 5):
                    v = float(getattr(event, "value", 0.0))
                    if v < -0.1:
                        norm = (v + 1.0) * 0.5
                    else:
                        norm = v
                    was_pressed = self._trigger_axis_pressed.get(axis, False)
                    is_pressed = norm >= self._trigger_deadzone
                    if is_pressed and not was_pressed:
                        self.game.player.dash()
                    self._trigger_axis_pressed[axis] = is_pressed

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
                if event.key in (pygame.K_s, pygame.K_DOWN):
                    self.game.player.trigger_slide_animation()

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
        # Poll joystick each frame so left-stick movement remains responsive.
        self._apply_horizontal_movement()

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
            if not getattr(self.game.player, "slide_ability_active", False):
                self.game.player.shoot()

        # Slide hold: keyboard S/Down or controller ability button (B)
        ability_held = self._controller_ability_held() or self._kbd_slide_held
        if ability_held:
            self.game.player.trigger_slide_animation()
        else:
            self.game.player.release_slide_animation()
        self._ability_was_down = ability_held

        # Dash / Black Mist:
        # - Dash: keyboard Space tap only
        # - Hover hold: keyboard Space or controller LB/RB
        # - Hold >= 2s: enable mist/hover while held
        keys = pygame.key.get_pressed()
        if getattr(self.game.player, "slide_ability_active", False):
            # During slide, block dash/hover controls entirely.
            self._space_was_down = bool(keys[pygame.K_SPACE])
            self._hover_was_down = False
            self._dash_was_down = self._space_was_down
            return
        space_held = bool(keys[pygame.K_SPACE])
        hover_held = space_held or self._controller_hover_held()
        now = pygame.time.get_ticks()
        if space_held and not self._space_was_down:
            self.game.player.dash()
        if hover_held and not self._hover_was_down:
            self._dash_down_since = now
            self._shadow_started_from_hold = False
        if hover_held and self._hover_was_down and not self._shadow_started_from_hold:
            if now - self._dash_down_since >= 2000 and hasattr(self.game.player, "set_shadow_form"):
                self.game.player.set_shadow_form(True)
                self._shadow_started_from_hold = True
        if (not hover_held) and self._hover_was_down:
            if hasattr(self.game.player, "set_shadow_form"):
                self.game.player.set_shadow_form(False)
            self._shadow_started_from_hold = False
        self._space_was_down = space_held
        self._hover_was_down = hover_held
        # Backward compatibility flag retained for any external references.
        self._dash_was_down = space_held
