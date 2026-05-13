import random
import math

import pygame


class Cloud:
    def __init__(self, pos, img, speed, depth):
        self.pos = list(pos)
        self.img = img
        self.speed = speed
        self.depth = depth

    def update(self):
        self.pos[0] += self.speed

    def render(self, surf, offset=(0, 0)):
        render_pos = (
            self.pos[0] - offset[0] * self.depth,
            self.pos[1] - offset[1] * self.depth,
        )
        surf.blit(
            self.img,
            (
                render_pos[0] % (surf.get_width() + self.img.get_width()) - self.img.get_width(),
                render_pos[1] % (surf.get_height() + self.img.get_height()) - self.img.get_height(),
            ),
        )


class Clouds:
    def __init__(self, cloud_images, count=16):
        self.clouds = []

        for i in range(count):
            self.clouds.append(
                Cloud(
                    (random.random() * 99999, random.random() * 99999),
                    random.choice(cloud_images),
                    random.random() * 0.05 + 0.05,
                    random.random() * 0.6 + 0.2,
                )
            )

        # sorting the clouds so front ones moving slower
        self.clouds.sort(key=lambda x: x.depth)

    def update(self):
        for cloud in self.clouds:
            cloud.update()

    def render(self, surf, offset=(0, 0)):
        for cloud in self.clouds:
            cloud.render(surf, offset=offset)

    def get_jumpthru_rects_around(self, world_rect, scroll, viewport_size):
        """Return nearby one-way cloud platform rects in world coordinates."""
        rects = []
        view_w, view_h = viewport_size
        scroll_x, scroll_y = scroll
        for cloud in self.clouds:
            img_w = cloud.img.get_width()
            img_h = cloud.img.get_height()
            period_x = view_w + img_w
            period_y = view_h + img_h

            base_x = cloud.pos[0] + scroll_x * (1 - cloud.depth) - img_w
            base_y = cloud.pos[1] + scroll_y * (1 - cloud.depth) - img_h

            kx = int(round((world_rect.centerx - base_x) / period_x))
            ky = int(round((world_rect.centery - base_y) / period_y))

            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    world_x = base_x + (kx + ox) * period_x
                    world_y = base_y + (ky + oy) * period_y
                    # Top third is the "landable" portion to feel like soft cloud tops.
                    top_band_h = max(4, img_h // 3)
                    platform = pygame.Rect(int(world_x), int(world_y), img_w, top_band_h)
                    if (
                        platform.right >= world_rect.left - 8
                        and platform.left <= world_rect.right + 8
                        and platform.bottom >= world_rect.top - 32
                        and platform.top <= world_rect.bottom + 32
                    ):
                        rects.append(platform)
        return rects
