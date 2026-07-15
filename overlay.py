# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Mouse-transparent budget cubes overlay with Pymunk physics."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional, Sequence

import pymunk
from aqt import mw
from aqt.qt import (
    QColor,
    QEvent,
    QFont,
    QObject,
    QPainter,
    QPaintEvent,
    QPen,
    QPoint,
    QRectF,
    Qt,
    QTimer,
    QWidget,
)

from .logger import log as _config_log
from .logger import log_path
from .utils import format_seconds

_PHYSICS_FPS = 48
_DT = 1.0 / _PHYSICS_FPS
_PHYSICS_SUBSTEPS = 3
_GRAVITY = 1800.0
_MAX_FALL_SPEED = 1200.0
_RESTITUTION = 0.35
_FRICTION = 0.85
_SETTLE_SPEED = 28.0
_CUBE_SIZE = 22.0
_SPAWN_JITTER = 40.0
_SPAWN_X_FRACTION = 0.2  # drop from ~1/5 of the way across the window
_SPAWN_X_JITTER = 8.0
_DESPAWN_FADE_SEC = 0.35
_SPIN_MIN = -8.0  # radians / sec
_SPIN_MAX = 8.0
_SPIN_DAMP = 0.992
_SLIDE_KICK = 220.0
_STICK_TIME_SEC = 0.25
_STICK_SPEED = 40.0
_MAX_PLATFORM_VIEW_WIDTH = 0.55
_MAX_COLLIDER_VIEW_AREA = 0.45
_MAX_COLLIDER_VIEW_HEIGHT = 0.55
# Temporary: draw card platforms + floor line for layout debugging.
_DEBUG_DRAW_COLLIDERS = False
_DEBUG_STATUS_INTERVAL_SEC = 1.0
# Bottom bar must sit in the lower half; earlier wrong mappings parked the floor near
# the top and cubes settled there permanently.
_MIN_FLOOR_HEIGHT_FRACTION = 0.45
_FLOOR_CHANGE_WAKE_PX = 8.0

# Collision types for Pymunk handlers
COLLIDER_TYPE = 1
CUBE_TYPE = 2
# Static segment radius. Too thin + high fall speed ⇒ tunneling through the floor.
_BOUNDARY_RADIUS = 2.0


def _log(message: str) -> None:
    """Always write while collider debug is on; otherwise respect debug_logging."""
    if not _DEBUG_DRAW_COLLIDERS:
        _config_log(message)
        return
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}\n"
    try:
        with open(log_path(), "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


# Prefer content bounds of #qa — the element itself is often viewport-sized.
_CARD_RECT_JS = """
(function () {
  function unionRects(rects) {
    if (!rects || !rects.length) return null;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    let any = false;
    for (const r of rects) {
      if (!r || r.width < 1 || r.height < 1) continue;
      any = true;
      minX = Math.min(minX, r.left);
      minY = Math.min(minY, r.top);
      maxX = Math.max(maxX, r.right);
      maxY = Math.max(maxY, r.bottom);
    }
    if (!any) return null;
    return {x: minX, y: minY, w: maxX - minX, h: maxY - minY};
  }

  const qa = document.querySelector("#qa") || document.querySelector(".card");
  if (!qa) return null;

  let box = null;
  try {
    const range = document.createRange();
    range.selectNodeContents(qa);
    box = unionRects(range.getClientRects());
  } catch (e) {}

  if (!box) {
    const kids = qa.children;
    if (kids && kids.length) {
      const childRects = [];
      for (let i = 0; i < kids.length; i++) {
        childRects.push(kids[i].getBoundingClientRect());
      }
      box = unionRects(childRects);
    }
  }

  if (!box) {
    const r = qa.getBoundingClientRect();
    box = {x: r.left, y: r.top, w: r.width, h: r.height};
  }

  box.vw = window.innerWidth || 1;
  box.vh = window.innerHeight || 1;
  return box;
})()
"""


def cube_count_for_seconds(seconds: int, chunk_seconds: int) -> int:
    chunk = max(1, int(chunk_seconds))
    return max(0, int(seconds) // chunk)


def max_cube_count(max_budget_seconds: int, chunk_seconds: int) -> int:
    return cube_count_for_seconds(max_budget_seconds, chunk_seconds)


def cubes_removed_crossing(old_seconds: int, new_seconds: int, chunk_seconds: int) -> int:
    old_n = cube_count_for_seconds(old_seconds, chunk_seconds)
    new_n = cube_count_for_seconds(new_seconds, chunk_seconds)
    return max(0, old_n - new_n)


def normalize_card_platform(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    vw: float,
    vh: float,
    cube_size: float = _CUBE_SIZE,
) -> Optional[tuple[float, float, float, float]]:
    """Clamp / reject card rects into a bounceable one-way platform, or None."""
    if w < 2 or h < 2:
        return None
    # Spawn-band ceilings trap falling cubes at the top of the window.
    if y < cube_size * 2:
        return None

    if vw > 0 and w / vw > _MAX_PLATFORM_VIEW_WIDTH:
        max_w = vw * _MAX_PLATFORM_VIEW_WIDTH
        cx = x + w / 2
        x = cx - max_w / 2
        w = max_w

    if vw > 0 and vh > 0:
        area_frac = (w * h) / max(1.0, vw * vh)
        height_frac = h / vh
        if area_frac > _MAX_COLLIDER_VIEW_AREA or height_frac > _MAX_COLLIDER_VIEW_HEIGHT:
            h = max(28.0, min(h * 0.15, vh * 0.18))

    if h < 2 or w < 2:
        return None
    return (x, y, w, h)


class Cube:
    """Wrapper mapping dynamic attributes to clean property gates on PyMunk Body."""

    def __init__(
        self,
        body: pymunk.Body,
        shape: pymunk.Poly,
        size: float = _CUBE_SIZE,
        despawn_t: Optional[float] = None,
        stick_time: float = 0.0,
    ) -> None:
        self.body = body
        self.shape = shape
        self.size = size
        self.despawn_t = despawn_t
        self.stick_time = stick_time

    @property
    def x(self) -> float:
        return self.body.position.x - self.size / 2

    @x.setter
    def x(self, val: float) -> None:
        self.body.position = (val + self.size / 2, self.body.position.y)

    @property
    def y(self) -> float:
        return self.body.position.y - self.size / 2

    @y.setter
    def y(self, val: float) -> None:
        self.body.position = (self.body.position.x, val + self.size / 2)

    @property
    def vx(self) -> float:
        return self.body.velocity.x

    @vx.setter
    def vx(self, val: float) -> None:
        self.body.velocity = (val, self.body.velocity.y)

    @property
    def vy(self) -> float:
        return self.body.velocity.y

    @vy.setter
    def vy(self, val: float) -> None:
        self.body.velocity = (self.body.velocity.x, val)

    @property
    def angle(self) -> float:
        return self.body.angle

    @angle.setter
    def angle(self, val: float) -> None:
        self.body.angle = val

    @property
    def omega(self) -> float:
        return self.body.angular_velocity

    @omega.setter
    def omega(self, val: float) -> None:
        self.body.angular_velocity = val

    @property
    def settled(self) -> bool:
        return self.body.is_sleeping

    @settled.setter
    def settled(self, val: bool) -> None:
        if val:
            self.body.sleep()
        else:
            self.body.activate()

    @property
    def alive(self) -> bool:
        return self.despawn_t is None or self.despawn_t > 0

    def rect(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.size, self.size)


def resolve_aabb_penetration(
    moving: tuple[float, float, float, float],
    solid: tuple[float, float, float, float],
) -> Optional[str]:
    """Kept unmodified for downstream backward compatibility."""
    cx, cy, cw, ch = moving
    ox, oy, ow, oh = solid
    if cx + cw <= ox or ox + ow <= cx or cy + ch <= oy or oy + oh <= cy:
        return None
    pen_left = (cx + cw) - ox
    pen_right = (ox + ow) - cx
    pen_top = (cy + ch) - oy
    pen_bottom = (oy + oh) - cy
    min_pen = min(pen_left, pen_right, pen_top, pen_bottom)
    if min_pen == pen_top:
        return "top"
    if min_pen == pen_bottom:
        return "bottom"
    if min_pen == pen_left:
        return "left"
    return "right"


@dataclass
class PhysicsWorld:
    width: float = 400.0
    height: float = 600.0
    floor_y: float = 600.0
    cubes: list[Cube] = field(default_factory=list)
    _colliders: list[tuple[float, float, float, float]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.space = pymunk.Space()
        self.space.gravity = (0.0, _GRAVITY)
        self.space.sleep_time_threshold = 0.5
        self.space.idle_speed_threshold = _SETTLE_SPEED

        self.wall_shapes: list[pymunk.Segment] = []
        self.platform_shapes: list[pymunk.Segment] = []

        self.update_boundaries()

        # One-way platforms (Pymunk 7+: set Arbiter.process_collision, do not return bool).
        self.space.on_collision(COLLIDER_TYPE, CUBE_TYPE, pre_solve=self._platform_pre_solve)

    @property
    def colliders(self) -> list[tuple[float, float, float, float]]:
        return self._colliders

    @colliders.setter
    def colliders(self, rects: list[tuple[float, float, float, float]]) -> None:
        self._colliders = list(rects)
        self.update_colliders()

    def _platform_pre_solve(self, arbiter: pymunk.Arbiter, space: pymunk.Space, data: object) -> None:
        """Let cubes bounce on platforms only when dropping onto the top surface."""
        # Y points down; normal from platform → cube points "up" (negative y) for top hits.
        normal = arbiter.contact_point_set.normal
        arbiter.process_collision = normal.y < -0.7

    def update_boundaries(self) -> None:
        """Construct walls and main floor segment matching space bounds."""
        for shape in list(self.wall_shapes):
            if shape in self.space.shapes:
                self.space.remove(shape)
        self.wall_shapes.clear()

        floor = self.effective_floor()
        r = _BOUNDARY_RADIUS

        left_wall = pymunk.Segment(self.space.static_body, (0, -_CUBE_SIZE * 2), (0, floor), r)
        left_wall.elasticity = _RESTITUTION
        left_wall.friction = _FRICTION

        right_wall = pymunk.Segment(
            self.space.static_body, (self.width, -_CUBE_SIZE * 2), (self.width, floor), r
        )
        right_wall.elasticity = _RESTITUTION
        right_wall.friction = _FRICTION

        floor_seg = pymunk.Segment(self.space.static_body, (0, floor), (self.width, floor), r)
        floor_seg.elasticity = _RESTITUTION
        floor_seg.friction = _FRICTION

        self.space.add(left_wall, right_wall, floor_seg)
        self.wall_shapes.extend([left_wall, right_wall, floor_seg])

    def update_colliders(self) -> None:
        """Parse platform coordinates into static top-edge segments (one-way)."""
        for shape in list(self.platform_shapes):
            if shape in self.space.shapes:
                self.space.remove(shape)
        self.platform_shapes.clear()

        for ox, oy, ow, oh in self._colliders:
            # Segment only — a filled poly lets cubes tunnel in while one-way
            # collisions are ignored, then explode out with a huge impulse.
            shape = pymunk.Segment(
                self.space.static_body,
                (ox, oy),
                (ox + ow, oy),
                _BOUNDARY_RADIUS,
            )
            shape.collision_type = COLLIDER_TYPE
            shape.elasticity = _RESTITUTION
            shape.friction = _FRICTION
            self.space.add(shape)
            self.platform_shapes.append(shape)

    def alive_cubes(self) -> list[Cube]:
        return [c for c in self.cubes if c.alive and c.despawn_t is None]

    def cube_count(self) -> int:
        return len(self.alive_cubes())

    def clear(self) -> None:
        for cube in self.cubes:
            if cube.shape in self.space.shapes:
                self.space.remove(cube.shape)
            if cube.body in self.space.bodies:
                self.space.remove(cube.body)
        self.cubes.clear()

    def effective_floor(self) -> float:
        return min(self.floor_y, self.height)

    def spawn_falling(self, *, size: float = _CUBE_SIZE) -> Cube:
        x = self.width * _SPAWN_X_FRACTION - size / 2
        x += random.uniform(-_SPAWN_X_JITTER, _SPAWN_X_JITTER)
        x = max(0.0, min(self.width - size, x))

        cx = x + size / 2
        cy = -size / 2 - random.uniform(0, _SPAWN_JITTER)

        mass = 1.0
        moment = pymunk.moment_for_box(mass, (size, size))
        body = pymunk.Body(mass, moment)
        body.position = (cx, cy)
        body.velocity = (random.uniform(-25, 25), random.uniform(0, 40))

        tau = math.tau if hasattr(math, "tau") else (2 * math.pi)
        body.angle = random.uniform(0, tau)
        body.angular_velocity = random.uniform(_SPIN_MIN, _SPIN_MAX)

        shape = pymunk.Poly.create_box(body, (size, size))
        shape.collision_type = CUBE_TYPE
        shape.elasticity = _RESTITUTION
        shape.friction = _FRICTION

        self.space.add(body, shape)

        cube = Cube(body, shape, size)
        self.cubes.append(cube)
        return cube

    def spawn_settled_pile(self, count: int, *, size: float = _CUBE_SIZE) -> None:
        """Place cubes in a rough pile on the physics floor."""
        if count <= 0:
            return
        floor = self.effective_floor()
        cols = max(1, int(self.width // (size + 2)))
        for i in range(count):
            col = i % cols
            row = i // cols
            x = 4 + col * (size + 2) + random.uniform(-1, 1)
            y = floor - size - 4 - row * (size + 1)
            y = max(0, y)

            cx = x + size / 2
            cy = y + size / 2

            mass = 1.0
            moment = pymunk.moment_for_box(mass, (size, size))
            body = pymunk.Body(mass, moment)
            body.position = (cx, cy)
            body.velocity = (0.0, 0.0)
            body.angle = random.uniform(0, math.pi / 8)
            body.angular_velocity = 0.0

            shape = pymunk.Poly.create_box(body, (size, size))
            shape.collision_type = CUBE_TYPE
            shape.elasticity = _RESTITUTION
            shape.friction = _FRICTION

            self.space.add(body, shape)

            cube = Cube(body, shape, size)
            self.cubes.append(cube)
            cube.settled = True  # Put directly to sleep

    def begin_despawn_random(self, n: int = 1) -> None:
        alive = self.alive_cubes()
        if not alive or n <= 0:
            return
        settled = [c for c in alive if c.settled]
        pool = settled if settled else alive
        n = min(n, len(pool))
        victims = random.sample(pool, n)
        for cube in victims:
            cube.settled = False
            cube.despawn_t = _DESPAWN_FADE_SEC
            # Mask out collisions on fading shapes immediately
            cube.shape.filter = pymunk.ShapeFilter(mask=0)

    def wake_all(self, *, reason: str = "") -> int:
        """Unsettle every cube so physics resumes (e.g. after floor moves)."""
        woken = 0
        for cube in self.alive_cubes():
            if cube.settled:
                cube.settled = False
                cube.stick_time = 0.0
                woken += 1
        if woken and _DEBUG_DRAW_COLLIDERS:
            _log(f"physics wake_all n={woken} reason={reason!r} floor={self.effective_floor():.1f}")
        return woken

    def wake_cubes_not_on_floor(self, *, reason: str = "") -> int:
        """Unsettle cubes that are clearly not resting on the current floor."""
        floor = self.effective_floor()
        woken = 0
        for cube in self.alive_cubes():
            if not cube.settled:
                continue
            dist = abs((cube.y + cube.size) - floor)
            if dist > cube.size * 1.5:
                cube.settled = False
                cube.stick_time = 0.0
                woken += 1
        if woken and _DEBUG_DRAW_COLLIDERS:
            _log(f"physics wake_off_floor n={woken} reason={reason!r} floor={floor:.1f}")
        return woken

    def rehome_for_floor_change(self, old_floor: float, *, reason: str = "") -> int:
        """Keep cubes attached when the physics floor moves.

        Screen Y grows downward. When the review bottom bar appears, floor_y
        decreases (rises on screen). Cubes sitting on the old bottom would be
        *below* the new floor segment and fall off-screen — lift them instead.
        When the floor drops, wake nearby cubes so they fall onto it.
        """
        new_floor = self.effective_floor()
        delta = new_floor - old_floor
        if abs(delta) < _FLOOR_CHANGE_WAKE_PX:
            return 0

        if delta < 0:
            # Floor rose: translate anything at/below the new line with it.
            lift = -delta
            moved = 0
            for cube in self.alive_cubes():
                bottom = cube.y + cube.size
                if bottom < new_floor - cube.size * 0.5:
                    continue  # mid-air / on a platform above the new floor
                was_settled = cube.settled
                cube.settled = False
                cube.y -= lift
                cube.vx = 0.0
                cube.vy = 0.0
                cube.omega = 0.0
                cube.stick_time = 0.0
                if was_settled:
                    cube.settled = True
                moved += 1
            if moved and _DEBUG_DRAW_COLLIDERS:
                _log(
                    f"physics rehome_lift n={moved} reason={reason!r} "
                    f"floor={old_floor:.1f}->{new_floor:.1f}"
                )
            return moved

        # Floor dropped: let cubes that are no longer supported fall.
        return self.wake_cubes_not_on_floor(reason=reason)

    def step(self, dt: float = _DT) -> None:
        remaining: list[Cube] = []
        for cube in self.cubes:
            if cube.despawn_t is not None:
                cube.despawn_t -= dt
                if cube.despawn_t > 0:
                    remaining.append(cube)
                else:
                    # Fade completed, clean out Pymunk space assets
                    if cube.shape in self.space.shapes:
                        self.space.remove(cube.shape)
                    if cube.body in self.space.bodies:
                        self.space.remove(cube.body)
                continue
            remaining.append(cube)
        self.cubes = remaining

        sub_dt = dt / _PHYSICS_SUBSTEPS
        for _ in range(_PHYSICS_SUBSTEPS):
            self.space.step(sub_dt)
            for cube in self.cubes:
                if cube.despawn_t is not None or cube.settled:
                    continue
                if cube.vy > _MAX_FALL_SPEED:
                    cube.vy = _MAX_FALL_SPEED

        for cube in self.cubes:
            if cube.despawn_t is not None:
                continue
            if cube.settled:
                cube.stick_time = 0.0
                continue

            # Clamp ceiling boundary
            if cube.y < -cube.size * 2:
                cube.y = -cube.size * 2
                cube.vy = max(0.0, cube.vy)

            # Spin damp — avoid writing omega≈0 every frame (wakes pymunk idle timer).
            if abs(cube.omega) > 1e-4:
                cube.omega *= _SPIN_DAMP

            # Sliding platform kick checks
            on_platform, platform_rect = self._is_on_platform(cube)
            speed = math.hypot(cube.vx, cube.vy)
            if on_platform and speed < _STICK_SPEED and abs(cube.vy) < _STICK_SPEED:
                cube.stick_time += dt
                if cube.stick_time >= _STICK_TIME_SEC:
                    self._kick_off_platform(cube, platform_rect)
                    cube.stick_time = 0.0
                    if _DEBUG_DRAW_COLLIDERS:
                        _log(f"cube stick-break kick vx={cube.vx:.1f} y={cube.y:.1f}")
            else:
                cube.stick_time = 0.0

    def _is_on_platform(self, cube: Cube) -> tuple[bool, Optional[tuple[float, float, float, float]]]:
        cx, cy, cw, ch = cube.rect()
        for platform in self.colliders:
            ox, oy, ow, oh = platform
            if cx + cw > ox and ox + ow > cx:
                if abs((cy + ch) - oy) < 2.5:
                    return True, platform
        return False, None

    def _kick_off_platform(
        self,
        cube: Cube,
        platform: Optional[tuple[float, float, float, float]],
    ) -> None:
        cx = cube.x + cube.size / 2
        if platform is None:
            direction = 1.0 if cx < self.width / 2 else -1.0
        else:
            ox, _oy, ow, _oh = platform
            mid = ox + ow / 2
            direction = -1.0 if cx <= mid else 1.0
        cube.settled = False
        cube.vx = direction * _SLIDE_KICK
        cube.vy = min(cube.vy, -80.0)
        cube.omega += direction * 4.0


class _ParentResizeFilter(QObject):
    def __init__(self, overlay: "BudgetOverlay") -> None:
        super().__init__(overlay)
        self._overlay = overlay

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self._overlay._sync_geometry()
        return False


class BudgetOverlay(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)

        self.world = PhysicsWorld()
        self._display_seconds = 0
        self._debug_status_accum = 0.0
        self._parent_filter = _ParentResizeFilter(self)
        parent.installEventFilter(self._parent_filter)

        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / _PHYSICS_FPS))
        self._timer.timeout.connect(self._on_tick)

        self._sync_geometry()
        self.show()
        self.raise_()
        self._timer.start()

    def shutdown(self) -> None:
        self._timer.stop()
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self._parent_filter)
        self.hide()
        self.deleteLater()

    def set_display_seconds(self, seconds: int) -> None:
        self._display_seconds = max(0, int(seconds))
        self.update()

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.rect())
        self.world.width = float(max(1, self.width()))
        self.world.height = float(max(1, self.height()))
        if self.world.floor_y <= 0 or self.world.floor_y > self.world.height:
            self.world.floor_y = self.world.height
        self.world.update_boundaries()
        self.raise_()

    def set_floor_y(self, floor_y: float) -> None:
        new_floor = max(1.0, min(float(floor_y), self.world.height))
        old_floor = self.world.floor_y
        self.world.floor_y = new_floor
        self.world.update_boundaries()
        if abs(new_floor - old_floor) >= _FLOOR_CHANGE_WAKE_PX:
            self.world.rehome_for_floor_change(
                old_floor, reason=f"floor {old_floor:.1f}->{new_floor:.1f}"
            )
            if _DEBUG_DRAW_COLLIDERS:
                _log(f"floor set {old_floor:.1f} -> {new_floor:.1f} (h={self.world.height:.1f})")
        self.update()

    def reset_floor_to_bottom(self) -> None:
        old_floor = self.world.floor_y
        self.world.floor_y = self.world.height
        self.world.update_boundaries()
        if abs(self.world.floor_y - old_floor) >= _FLOOR_CHANGE_WAKE_PX:
            self.world.wake_all(reason="reset_floor_to_bottom")
            if _DEBUG_DRAW_COLLIDERS:
                _log(f"floor reset to height={self.world.height:.1f}")
        self.update()

    def wake_all_cubes(self, *, reason: str = "") -> None:
        self.world.wake_all(reason=reason)
        self.update()

    def set_colliders(self, rects: Sequence[tuple[float, float, float, float]]) -> None:
        self.world.colliders = [
            (float(x), float(y), float(w), float(h)) for x, y, w, h in rects if w > 1 and h > 1
        ]
        self.update()

    def clear_colliders(self) -> None:
        self.world.colliders = []
        self.update()

    def hydrate_settled(self, count: int) -> None:
        self.world.clear()
        self.world.spawn_settled_pile(count)
        self.update()

    def sync_to_count(self, target: int, *, falling: bool) -> None:
        target = max(0, int(target))
        current = self.world.cube_count()
        if current > target:
            self.world.begin_despawn_random(current - target)
        elif current < target:
            n = target - current
            for _ in range(n):
                if falling:
                    cube = self.world.spawn_falling()
                    if _DEBUG_DRAW_COLLIDERS:
                        _log(
                            f"spawn falling x={cube.x:.1f} y={cube.y:.1f} "
                            f"vy={cube.vy:.1f} floor={self.world.effective_floor():.1f}"
                        )
                else:
                    self.world.spawn_settled_pile(1)
        self.update()

    def _on_tick(self) -> None:
        if self.world.cubes:
            self.world.step(_DT)
        if _DEBUG_DRAW_COLLIDERS:
            self._debug_status_accum += _DT
            if self._debug_status_accum >= _DEBUG_STATUS_INTERVAL_SEC:
                self._debug_status_accum = 0.0
                self._log_debug_status()
        # Keep redrawing while debugging so platforms stay visible with 0 cubes.
        if self.world.cubes or _DEBUG_DRAW_COLLIDERS:
            self.update()

    def _log_debug_status(self) -> None:
        alive = self.world.alive_cubes()
        if not alive and not self.world.colliders:
            return
        settled_n = sum(1 for c in alive if c.settled)
        sample = alive[:3]
        parts = [
            f"n={len(alive)} settled={settled_n} "
            f"floor={self.world.effective_floor():.1f} h={self.world.height:.1f} "
            f"colliders={len(self.world.colliders)}"
        ]
        for i, c in enumerate(sample):
            parts.append(f"c{i}[y={c.y:.0f} vy={c.vy:.0f} settled={c.settled}]")
        _log("overlay status " + " ".join(parts))

    def _paint_debug_colliders(self, painter: QPainter) -> None:
        floor = self.world.effective_floor()
        # Floor line (physics ground).
        painter.setPen(QPen(QColor(255, 80, 80, 220), 2, Qt.PenStyle.DashLine))
        painter.drawLine(0, int(floor), self.width(), int(floor))
        painter.setPen(QColor(255, 80, 80, 200))
        painter.drawText(
            12,
            max(14, int(floor) - 6),
            f"floor y={floor:.0f} h={self.world.height:.0f}",
        )

        # Card / one-way platforms.
        for i, (x, y, w, h) in enumerate(self.world.colliders):
            painter.setBrush(QColor(255, 200, 40, 55))
            painter.setPen(QPen(QColor(255, 160, 0, 230), 2))
            painter.drawRect(QRectF(x, y, w, h))
            painter.setPen(QColor(255, 220, 120, 240))
            painter.drawText(
                QRectF(x + 4, y + 2, max(40.0, w - 8), 16),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                f"c{i} {w:.0f}x{h:.0f} @({x:.0f},{y:.0f})",
            )

        # Spawn column hint.
        spawn_x = self.world.width * _SPAWN_X_FRACTION
        painter.setPen(QPen(QColor(80, 200, 255, 160), 1, Qt.PenStyle.DotLine))
        painter.drawLine(int(spawn_x), 0, int(spawn_x), int(floor))

        # Per-cube debug outline: magenta = settled (frozen), cyan = active.
        for cube in self.world.cubes:
            if not cube.alive:
                continue
            color = QColor(255, 40, 200, 220) if cube.settled else QColor(40, 255, 220, 200)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color, 2))
            painter.drawRect(QRectF(cube.x, cube.y, cube.size, cube.size))
            painter.drawText(
                int(cube.x + cube.size + 2),
                int(cube.y + 12),
                f"{'SET' if cube.settled else 'fall'} y={cube.y:.0f} vy={cube.vy:.0f}",
            )

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if _DEBUG_DRAW_COLLIDERS:
            self._paint_debug_colliders(painter)

        label = f"Watch: {format_seconds(self._display_seconds)}"
        font = QFont(painter.font())
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(label)
        text_h = metrics.height()
        pad_x, pad_y = 8, 5
        hud = QRectF(10, 10, text_w + pad_x * 2, text_h + pad_y * 2)
        painter.setBrush(QColor(20, 20, 20, 140))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(hud, 6, 6)
        painter.setPen(QColor(245, 245, 245, 235))
        painter.drawText(
            QRectF(hud.x() + pad_x, hud.y() + pad_y, text_w, text_h),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            label,
        )

        for cube in self.world.cubes:
            if not cube.alive:
                continue
            alpha = 220
            if cube.despawn_t is not None:
                alpha = max(0, int(220 * (cube.despawn_t / _DESPAWN_FADE_SEC)))
            color = QColor(70, 140, 220, alpha)
            border = QColor(30, 80, 160, alpha)
            painter.setBrush(color)
            painter.setPen(border)
            radius = max(2.0, cube.size * 0.18)
            scale = 1.0
            if cube.despawn_t is not None:
                scale = max(0.15, cube.despawn_t / _DESPAWN_FADE_SEC)
            cx = cube.x + cube.size / 2
            cy = cube.y + cube.size / 2
            s = cube.size * scale
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(math.degrees(cube.angle))
            rect = QRectF(-s / 2, -s / 2, s, s)
            painter.drawRoundedRect(rect, radius * scale, radius * scale)
            painter.restore()
        painter.end()


class BudgetOverlayController:
    """Owns the overlay widget and keeps cube count aligned with budget chunks."""

    def __init__(self, addon_module: str) -> None:
        self._addon_module = addon_module
        self._overlay: Optional[BudgetOverlay] = None
        self._collider_poll = QTimer()
        self._collider_poll.setInterval(350)
        self._collider_poll.timeout.connect(self.refresh_card_colliders)
        self._last_seconds: Optional[int] = None
        self._chunk = 15

    @property
    def overlay(self) -> Optional[BudgetOverlay]:
        return self._overlay

    def start(self) -> None:
        central = mw.centralWidget()
        if central is None:
            _log("budget overlay: no central widget yet")
            return
        if self._overlay is not None:
            return
        self._overlay = BudgetOverlay(central)
        _log("budget overlay started")

    def shutdown(self) -> None:
        self._collider_poll.stop()
        if self._overlay is not None:
            self._overlay.shutdown()
            self._overlay = None
        self._last_seconds = None
        _log("budget overlay shut down")

    def ensure_raised(self) -> None:
        if self._overlay is not None:
            self._overlay._sync_geometry()
            self._overlay.raise_()

    def set_review_active(self, active: bool) -> None:
        if active:
            if not self._collider_poll.isActive():
                self._collider_poll.start()
            QTimer.singleShot(0, self.refresh_card_colliders)
            QTimer.singleShot(50, self.refresh_card_colliders)
        else:
            self._collider_poll.stop()
            if self._overlay is not None:
                self._overlay.clear_colliders()
                self._overlay.reset_floor_to_bottom()
                self._overlay.wake_all_cubes(reason="leave_review")
        self.ensure_raised()

    def hydrate_from_budget(self, seconds: int, chunk_seconds: int, max_budget_seconds: int) -> None:
        self._chunk = max(1, int(chunk_seconds))
        self._last_seconds = int(seconds)
        if self._overlay is None:
            self.start()
        if self._overlay is None:
            return
        self._overlay.set_display_seconds(seconds)
        # Ensure floor is ready before stacking hydrate piles.
        self.refresh_floor()
        cap = max_cube_count(max_budget_seconds, self._chunk)
        count = min(cube_count_for_seconds(seconds, self._chunk), cap)
        self._overlay.hydrate_settled(count)

    def on_budget_seconds(
        self,
        seconds: int,
        chunk_seconds: int,
        max_budget_seconds: int,
        *,
        falling: bool,
    ) -> None:
        self._chunk = max(1, int(chunk_seconds))
        cap = max_cube_count(max_budget_seconds, self._chunk)
        target = min(cube_count_for_seconds(seconds, self._chunk), cap)
        if self._overlay is None:
            self.start()
        if self._overlay is None:
            return
        self._overlay.set_display_seconds(seconds)
        if falling:
            self.refresh_floor()
        self._overlay.sync_to_count(target, falling=falling)
        self._last_seconds = int(seconds)

    def refresh_floor(self) -> None:
        if self._overlay is None:
            return
        if mw.state != "review":
            self._overlay.reset_floor_to_bottom()
            return
        reviewer = getattr(mw, "reviewer", None)
        bottom = getattr(reviewer, "bottom", None) if reviewer is not None else None
        bottom_web = getattr(bottom, "web", None) if bottom is not None else None
        if bottom_web is None:
            self._overlay.reset_floor_to_bottom()
            return
        try:
            top_left = self._overlay.mapFromGlobal(bottom_web.mapToGlobal(QPoint(0, 0)))
            floor_y = float(top_left.y())
            height = self._overlay.world.height
            if floor_y < 40 or floor_y > height or floor_y < height * _MIN_FLOOR_HEIGHT_FRACTION:
                if _DEBUG_DRAW_COLLIDERS:
                    _log(f"floor rejected y={floor_y:.1f} h={height:.1f} (need >= {height * _MIN_FLOOR_HEIGHT_FRACTION:.1f})")
                self._overlay.reset_floor_to_bottom()
                return
            self._overlay.set_floor_y(floor_y)
        except Exception as exc:
            _log(f"floor refresh failed: {exc}")
            self._overlay.reset_floor_to_bottom()

    def refresh_card_colliders(self) -> None:
        if self._overlay is None:
            return
        self.refresh_floor()
        if mw.state != "review":
            self._overlay.clear_colliders()
            return
        reviewer = getattr(mw, "reviewer", None)
        web = getattr(reviewer, "web", None) if reviewer is not None else None
        if web is None:
            self._overlay.clear_colliders()
            return

        def on_result(result: object) -> None:
            if self._overlay is None:
                return
            if not isinstance(result, dict):
                self._overlay.clear_colliders()
                return
            try:
                x = float(result.get("x", 0))
                y = float(result.get("y", 0))
                w = float(result.get("w", 0))
                h = float(result.get("h", 0))
                vw = float(result.get("vw", 0) or 0)
                vh = float(result.get("vh", 0) or 0)
            except (TypeError, ValueError, AttributeError):
                self._overlay.clear_colliders()
                return
            try:
                origin = self._overlay.mapFromGlobal(web.mapToGlobal(QPoint(0, 0)))
            except Exception:
                self._overlay.clear_colliders()
                return
            platform = normalize_card_platform(
                origin.x() + x,
                origin.y() + y,
                w,
                h,
                vw=vw if vw > 0 else float(web.width()),
                vh=vh if vh > 0 else float(web.height()),
            )
            if platform is None:
                _log("card collider rejected (spawn-band or empty)")
                self._overlay.clear_colliders()
                return
            self._overlay.set_colliders([platform])
            self.ensure_raised()

        try:
            web.evalWithCallback(_CARD_RECT_JS, on_result)
        except Exception as exc:
            _log(f"card collider query failed: {exc}")
            self._overlay.clear_colliders()
