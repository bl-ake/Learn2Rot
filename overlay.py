# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Budget cubes overlay with Pymunk physics and click-drag interaction."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional, Sequence

import pymunk
from aqt import mw
from aqt.qt import (
    QApplication,
    QColor,
    QElapsedTimer,
    QEvent,
    QFont,
    QMouseEvent,
    QObject,
    QPainter,
    QPaintEvent,
    QPen,
    QPoint,
    QRect,
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
_MAX_PHYSICS_CATCHUP_STEPS = 4
_MAX_FRAME_SEC = _DT * _MAX_PHYSICS_CATCHUP_STEPS
_GRAVITY = 1800.0
_MAX_FALL_SPEED = 1200.0
_RESTITUTION = 0.35
_FRICTION = 0.85
_SETTLE_SPEED = 28.0
_CUBE_SIZE = 22.0
_SPAWN_JITTER = 40.0
_MIN_BOUNDS_WIDTH_FRAC = 0.05  # keep at least 5% of window width
_DESPAWN_FADE_SEC = 0.35
_SPIN_MIN = 3.0  # radians / sec (magnitude; sign chosen at random)
_SPIN_MAX = 10.0
_SPIN_DAMP = 0.992
# Temporary: draw floor line + fill band for layout debugging.
_DEBUG_DRAW_COLLIDERS = False
_DEBUG_STATUS_INTERVAL_SEC = 1.0
# Bottom bar must sit in the lower half; earlier wrong mappings parked the floor near
# the top and cubes settled there permanently.
_MIN_FLOOR_HEIGHT_FRACTION = 0.45
_FLOOR_CHANGE_WAKE_PX = 8.0
_DRAG_MAX_FORCE = 1.2e5

# Collision type for cube shapes in Pymunk.
CUBE_TYPE = 2
# Static segment radius. Too thin + high fall speed ⇒ tunneling through the floor.
_BOUNDARY_RADIUS = 2.0


def consume_physics_time(
    world: "PhysicsWorld",
    accum: float,
    *,
    dt: float = _DT,
    max_steps: int = _MAX_PHYSICS_CATCHUP_STEPS,
) -> float:
    """Advance *world* by fixed *dt* steps; return leftover accumulator time."""
    steps = 0
    while accum >= dt and steps < max_steps:
        world.step(dt)
        accum -= dt
        steps += 1
    if steps >= max_steps and accum > dt:
        # Drop backlog after a hitch so we don't spiral.
        accum = 0.0
    return accum


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


def cube_count_for_seconds(seconds: int, chunk_seconds: int) -> int:
    chunk = max(1, int(chunk_seconds))
    return max(0, int(seconds) // chunk)


def max_cube_count(max_budget_seconds: int, chunk_seconds: int) -> int:
    return cube_count_for_seconds(max_budget_seconds, chunk_seconds)


def cubes_removed_crossing(old_seconds: int, new_seconds: int, chunk_seconds: int) -> int:
    old_n = cube_count_for_seconds(old_seconds, chunk_seconds)
    new_n = cube_count_for_seconds(new_seconds, chunk_seconds)
    return max(0, old_n - new_n)


class Cube:
    """Wrapper mapping dynamic attributes to clean property gates on PyMunk Body."""

    def __init__(
        self,
        body: pymunk.Body,
        shape: pymunk.Poly,
        size: float = _CUBE_SIZE,
        despawn_t: Optional[float] = None,
    ) -> None:
        self.body = body
        self.shape = shape
        self.size = size
        self.despawn_t = despawn_t

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


@dataclass
class PhysicsWorld:
    width: float = 400.0
    height: float = 600.0
    floor_y: float = 600.0
    # Horizontal fill band as fractions of width (0–1). Cubes spawn and pile here.
    bounds_left_frac: float = 0.0
    bounds_right_frac: float = 1.0
    cubes: list[Cube] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.space = pymunk.Space()
        self.space.gravity = (0.0, _GRAVITY)
        self.space.sleep_time_threshold = 0.5
        self.space.idle_speed_threshold = _SETTLE_SPEED

        self.wall_shapes: list[pymunk.Segment] = []
        self._drag_cube: Optional[Cube] = None
        self._mouse_body: Optional[pymunk.Body] = None
        self._drag_joint: Optional[pymunk.PivotJoint] = None
        self._drag_target: Optional[tuple[float, float]] = None

        self.set_horizontal_bounds(self.bounds_left_frac, self.bounds_right_frac)

    def set_horizontal_bounds(self, left_frac: float, right_frac: float) -> None:
        """Clamp and apply the horizontal band cubes may fill; rebuilds walls."""
        left = max(0.0, min(1.0, float(left_frac)))
        right = max(0.0, min(1.0, float(right_frac)))
        if right - left < _MIN_BOUNDS_WIDTH_FRAC:
            mid = (left + right) / 2.0
            left = max(0.0, mid - _MIN_BOUNDS_WIDTH_FRAC / 2.0)
            right = min(1.0, left + _MIN_BOUNDS_WIDTH_FRAC)
            if right - left < _MIN_BOUNDS_WIDTH_FRAC:
                left = max(0.0, right - _MIN_BOUNDS_WIDTH_FRAC)
        self.bounds_left_frac = left
        self.bounds_right_frac = right
        self.update_boundaries()

    def fill_x_range(self, size: float = _CUBE_SIZE) -> tuple[float, float]:
        """Inclusive min/max for a cube's left edge within the fill bounds."""
        left = self.width * self.bounds_left_frac
        right = self.width * self.bounds_right_frac
        # Keep the cube fully inside the walls (walls sit on the bound edges).
        max_left = max(left, right - size)
        return left, max_left

    def update_boundaries(self) -> None:
        """Construct walls and main floor segment matching the fill bounds."""
        for shape in list(self.wall_shapes):
            if shape in self.space.shapes:
                self.space.remove(shape)
        self.wall_shapes.clear()

        floor = self.effective_floor()
        r = _BOUNDARY_RADIUS
        left_x = self.width * self.bounds_left_frac
        right_x = self.width * self.bounds_right_frac

        left_wall = pymunk.Segment(self.space.static_body, (left_x, -_CUBE_SIZE * 2), (left_x, floor), r)
        left_wall.elasticity = _RESTITUTION
        left_wall.friction = _FRICTION

        right_wall = pymunk.Segment(self.space.static_body, (right_x, -_CUBE_SIZE * 2), (right_x, floor), r)
        right_wall.elasticity = _RESTITUTION
        right_wall.friction = _FRICTION

        floor_seg = pymunk.Segment(self.space.static_body, (left_x, floor), (right_x, floor), r)
        floor_seg.elasticity = _RESTITUTION
        floor_seg.friction = _FRICTION

        self.space.add(left_wall, right_wall, floor_seg)
        self.wall_shapes.extend([left_wall, right_wall, floor_seg])

    def alive_cubes(self) -> list[Cube]:
        return [c for c in self.cubes if c.alive and c.despawn_t is None]

    def cube_count(self) -> int:
        return len(self.alive_cubes())

    def clear(self) -> None:
        self.end_drag()
        for cube in self.cubes:
            if cube.shape in self.space.shapes:
                self.space.remove(cube.shape)
            if cube.body in self.space.bodies:
                self.space.remove(cube.body)
        self.cubes.clear()

    def effective_floor(self) -> float:
        return min(self.floor_y, self.height)

    @property
    def drag_cube(self) -> Optional[Cube]:
        return self._drag_cube

    def hit_test(self, x: float, y: float) -> Optional[Cube]:
        """Return the topmost living cube under a point, if any."""
        info = self.space.point_query_nearest((x, y), 0.0, pymunk.ShapeFilter())
        if info is None or info.shape is None:
            return None
        for cube in reversed(self.cubes):
            if cube.shape is info.shape and cube.alive and cube.despawn_t is None:
                return cube
        return None

    def begin_drag(self, cube: Cube, x: float, y: float) -> None:
        if cube not in self.cubes or not cube.alive or cube.despawn_t is not None:
            return
        self.end_drag()
        cube.settled = False
        mouse_body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        mouse_body.position = (x, y)
        local = cube.body.world_to_local((x, y))
        joint = pymunk.PivotJoint(mouse_body, cube.body, (0, 0), local)
        joint.max_force = _DRAG_MAX_FORCE
        joint.error_bias = (1.0 - 0.2) ** 60
        self.space.add(joint)
        self._mouse_body = mouse_body
        self._drag_joint = joint
        self._drag_cube = cube
        self._drag_target = (x, y)

    def update_drag(self, x: float, y: float) -> None:
        if self._mouse_body is None:
            return
        self._drag_target = (x, y)
        if self._drag_cube is not None:
            self._drag_cube.settled = False

    def end_drag(self) -> None:
        if self._drag_joint is not None and self._drag_joint in self.space.constraints:
            self.space.remove(self._drag_joint)
        self._drag_joint = None
        self._mouse_body = None
        self._drag_cube = None
        self._drag_target = None

    def _sync_mouse_body(self, dt: float) -> None:
        if self._mouse_body is None or self._drag_target is None:
            return
        tx, ty = self._drag_target
        old = self._mouse_body.position
        if dt > 1e-6:
            self._mouse_body.velocity = ((tx - old.x) / dt, (ty - old.y) / dt)
        self._mouse_body.position = (tx, ty)

    def spawn_falling(self, *, size: float = _CUBE_SIZE) -> Cube:
        x_min, x_max = self.fill_x_range(size)
        x = random.uniform(x_min, x_max)

        cx = x + size / 2
        cy = -size / 2 - random.uniform(0, _SPAWN_JITTER)

        mass = 10.0
        moment = pymunk.moment_for_box(mass, (size, size))
        body = pymunk.Body(mass, moment)
        body.position = (cx, cy)
        body.velocity = (random.uniform(-25, 25), random.uniform(0, 40))

        tau = math.tau if hasattr(math, "tau") else (2 * math.pi)
        body.angle = random.uniform(0, tau)
        # Bias away from zero so every falling cube visibly tumbles.
        spin = random.uniform(_SPIN_MIN, _SPIN_MAX)
        body.angular_velocity = spin if random.random() < 0.5 else -spin

        shape = pymunk.Poly.create_box(body, (size, size))
        shape.collision_type = CUBE_TYPE
        shape.elasticity = _RESTITUTION
        shape.friction = _FRICTION

        self.space.add(body, shape)

        cube = Cube(body, shape, size)
        self.cubes.append(cube)
        return cube

    def spawn_settled_pile(self, count: int, *, size: float = _CUBE_SIZE) -> None:
        """Place cubes in a rough pile on the physics floor within fill bounds."""
        if count <= 0:
            return
        floor = self.effective_floor()
        x_min, x_max = self.fill_x_range(size)
        band = max(size, (x_max - x_min) + size)
        cols = max(1, int(band // (size + 2)))
        for i in range(count):
            col = i % cols
            row = i // cols
            x = x_min + col * (size + 2) + random.uniform(-1, 1)
            x = max(x_min, min(x_max, x))
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
        candidates = [c for c in alive if c is not self._drag_cube]
        if not candidates:
            return
        settled = [c for c in candidates if c.settled]
        pool = settled if settled else candidates
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
                    continue  # mid-air above the new floor
                was_settled = cube.settled
                cube.settled = False
                cube.y -= lift
                cube.vx = 0.0
                cube.vy = 0.0
                cube.omega = 0.0
                if was_settled:
                    cube.settled = True
                moved += 1
            if moved and _DEBUG_DRAW_COLLIDERS:
                _log(f"physics rehome_lift n={moved} reason={reason!r} floor={old_floor:.1f}->{new_floor:.1f}")
            return moved

        # Floor dropped: let cubes that are no longer supported fall.
        return self.wake_cubes_not_on_floor(reason=reason)

    def step(self, dt: float = _DT) -> None:
        remaining: list[Cube] = []
        for cube in self.cubes:
            if cube.despawn_t is not None:
                if cube is self._drag_cube:
                    self.end_drag()
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
            self._sync_mouse_body(sub_dt)
            self.space.step(sub_dt)
            for cube in self.cubes:
                if cube.despawn_t is not None or cube.settled or cube is self._drag_cube:
                    continue
                if cube.vy > _MAX_FALL_SPEED:
                    cube.vy = _MAX_FALL_SPEED

        for cube in self.cubes:
            if cube.despawn_t is not None or cube is self._drag_cube or cube.settled:
                continue

            # Clamp ceiling boundary
            if cube.y < -cube.size * 2:
                cube.y = -cube.size * 2
                cube.vy = max(0.0, cube.vy)

            # Spin damp — avoid writing omega≈0 every frame (wakes pymunk idle timer).
            if abs(cube.omega) > 1e-4:
                cube.omega *= _SPIN_DAMP


class _HostGeometryFilter(QObject):
    """Keep the overlay tool window aligned when Anki's host widgets move/resize."""

    def __init__(self, overlay: "BudgetOverlay") -> None:
        super().__init__(overlay)
        self._overlay = overlay

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        etype = event.type()
        if etype in (
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.Show,
            QEvent.Type.WindowStateChange,
        ):
            self._overlay._sync_geometry()
        return False


class _OverlayInputFilter(QObject):
    """Pick and drag cubes while the overlay stays mouse-transparent to Anki."""

    def __init__(self, overlay: "BudgetOverlay") -> None:
        super().__init__(overlay)
        self._overlay = overlay

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        etype = event.type()
        if etype not in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseMove,
        ):
            return False
        if not isinstance(event, QMouseEvent):
            return False
        return self._overlay._handle_filtered_mouse(event)


class BudgetOverlay(QWidget):
    """Frameless tool window over Anki's central widget.

    Hosted as a native tool window (not an in-process child) so translucent
    clears work on every platform. Child widgets with WA_TranslucentBackground
    often lack a real alpha surface on Windows and paint opaque black instead.

    Always mouse-transparent: cube picking goes through an app-level filter so
    we never need setMask (which is slow on Windows and causes motion trails).
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)

        self.world = PhysicsWorld()
        self._display_seconds = 0
        self._show_timer = True
        self._debug_status_accum = 0.0
        self._physics_accum = 0.0
        self._dragging = False
        self._hover_cursor = False
        self._clock = QElapsedTimer()
        self._host_filter = _HostGeometryFilter(self)
        self._input_filter = _OverlayInputFilter(self)
        self._host_widgets: list[QWidget] = []
        self._app_filter_installed = False
        self._install_host_filters(parent)
        self._install_input_filter()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(int(1000 / _PHYSICS_FPS))
        self._timer.timeout.connect(self._on_tick)

        self._sync_geometry()
        self.show()
        self.raise_()
        self._clock.start()
        self._timer.start()
        self._request_repaint()

    def _install_host_filters(self, host: QWidget) -> None:
        targets = [host]
        top = host.window()
        if top is not None and top is not host:
            targets.append(top)
        for widget in targets:
            widget.installEventFilter(self._host_filter)
            self._host_widgets.append(widget)

    def _remove_host_filters(self) -> None:
        for widget in self._host_widgets:
            try:
                widget.removeEventFilter(self._host_filter)
            except RuntimeError:
                pass
        self._host_widgets.clear()

    def _install_input_filter(self) -> None:
        app = QApplication.instance()
        if app is None or self._app_filter_installed:
            return
        app.installEventFilter(self._input_filter)
        self._app_filter_installed = True

    def _remove_input_filter(self) -> None:
        if not self._app_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self._input_filter)
            except RuntimeError:
                pass
        self._app_filter_installed = False

    def _clear_hover_cursor(self) -> None:
        if not self._hover_cursor:
            return
        QApplication.restoreOverrideCursor()
        self._hover_cursor = False

    def _set_hover_cursor(self, shape: Qt.CursorShape) -> None:
        if self._hover_cursor:
            QApplication.changeOverrideCursor(shape)
        else:
            QApplication.setOverrideCursor(shape)
            self._hover_cursor = True

    def shutdown(self) -> None:
        if self._dragging:
            self._dragging = False
            self.world.end_drag()
        self._clear_hover_cursor()
        self._timer.stop()
        self._remove_input_filter()
        self._remove_host_filters()
        self.hide()
        self.deleteLater()

    def set_display_seconds(self, seconds: int) -> None:
        self._display_seconds = max(0, int(seconds))
        self._request_repaint()

    def set_show_timer(self, show: bool) -> None:
        self._show_timer = bool(show)
        self._request_repaint()

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        # Tool window geometry is in global coordinates.
        top_left = parent.mapToGlobal(QPoint(0, 0))
        self.setGeometry(QRect(top_left, parent.size()))
        self.world.width = float(max(1, self.width()))
        self.world.height = float(max(1, self.height()))
        if self.world.floor_y <= 0 or self.world.floor_y > self.world.height:
            self.world.floor_y = self.world.height
        self.world.update_boundaries()
        self.raise_()
        self._request_repaint()

    def _global_pos(self, event: QMouseEvent) -> QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def _local_from_global(self, global_pos: QPoint) -> QPoint:
        return self.mapFromGlobal(global_pos)

    def _handle_filtered_mouse(self, event: QMouseEvent) -> bool:
        """Handle cube pick/drag from the app filter. Returns True if consumed."""
        try:
            if not self.isVisible():
                return False
        except RuntimeError:
            return False

        etype = event.type()
        global_pos = self._global_pos(event)
        local = self._local_from_global(global_pos)
        inside = self.rect().contains(local)

        if self._dragging:
            if etype == QEvent.Type.MouseMove:
                self.world.update_drag(float(local.x()), float(local.y()))
                self._request_repaint()
                return True
            if (
                etype == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self.world.update_drag(float(local.x()), float(local.y()))
                self.world.end_drag()
                self._dragging = False
                self._clear_hover_cursor()
                if inside and self.world.hit_test(float(local.x()), float(local.y())) is not None:
                    self._set_hover_cursor(Qt.CursorShape.OpenHandCursor)
                self._request_repaint()
                return True
            return True

        if not inside:
            self._clear_hover_cursor()
            return False

        px, py = float(local.x()), float(local.y())
        cube = self.world.hit_test(px, py)

        if etype == QEvent.Type.MouseMove:
            if cube is not None:
                self._set_hover_cursor(Qt.CursorShape.OpenHandCursor)
            else:
                self._clear_hover_cursor()
            return False

        if (
            etype == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and cube is not None
        ):
            self.world.begin_drag(cube, px, py)
            self._dragging = True
            self._set_hover_cursor(Qt.CursorShape.ClosedHandCursor)
            self._request_repaint()
            return True

        return False

    def _request_repaint(self) -> None:
        self.update()

    def set_floor_y(self, floor_y: float) -> None:
        new_floor = max(1.0, min(float(floor_y), self.world.height))
        old_floor = self.world.floor_y
        self.world.floor_y = new_floor
        self.world.update_boundaries()
        if abs(new_floor - old_floor) >= _FLOOR_CHANGE_WAKE_PX:
            self.world.rehome_for_floor_change(old_floor, reason=f"floor {old_floor:.1f}->{new_floor:.1f}")
            if _DEBUG_DRAW_COLLIDERS:
                _log(f"floor set {old_floor:.1f} -> {new_floor:.1f} (h={self.world.height:.1f})")
        self._request_repaint()

    def reset_floor_to_bottom(self) -> None:
        old_floor = self.world.floor_y
        self.world.floor_y = self.world.height
        self.world.update_boundaries()
        if abs(self.world.floor_y - old_floor) >= _FLOOR_CHANGE_WAKE_PX:
            self.world.wake_all(reason="reset_floor_to_bottom")
            if _DEBUG_DRAW_COLLIDERS:
                _log(f"floor reset to height={self.world.height:.1f}")
        self._request_repaint()

    def wake_all_cubes(self, *, reason: str = "") -> None:
        self.world.wake_all(reason=reason)
        self._request_repaint()

    def hydrate_settled(self, count: int) -> None:
        if self._dragging:
            self._dragging = False
            self.world.end_drag()
            self._clear_hover_cursor()
        self.world.clear()
        self.world.spawn_settled_pile(count)
        self._request_repaint()

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
        self._request_repaint()

    def _on_tick(self) -> None:
        elapsed = self._clock.restart() / 1000.0
        frame = min(max(0.0, elapsed), _MAX_FRAME_SEC)
        if self.world.cubes:
            self._physics_accum += frame
            self._physics_accum = consume_physics_time(self.world, self._physics_accum)
        else:
            self._physics_accum = 0.0
        if _DEBUG_DRAW_COLLIDERS:
            self._debug_status_accum += frame
            if self._debug_status_accum >= _DEBUG_STATUS_INTERVAL_SEC:
                self._debug_status_accum = 0.0
                self._log_debug_status()
        # Keep redrawing while debugging so the floor line stays visible with 0 cubes.
        if self.world.cubes or _DEBUG_DRAW_COLLIDERS:
            self._request_repaint()

    def _log_debug_status(self) -> None:
        alive = self.world.alive_cubes()
        if not alive:
            return
        settled_n = sum(1 for c in alive if c.settled)
        sample = alive[:3]
        parts = [f"n={len(alive)} settled={settled_n} floor={self.world.effective_floor():.1f} h={self.world.height:.1f}"]
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

        # Spawn / fill band hint.
        left_x = self.world.width * self.world.bounds_left_frac
        right_x = self.world.width * self.world.bounds_right_frac
        painter.setPen(QPen(QColor(80, 200, 255, 160), 1, Qt.PenStyle.DotLine))
        painter.drawLine(int(left_x), 0, int(left_x), int(floor))
        painter.drawLine(int(right_x), 0, int(right_x), int(floor))
        painter.setBrush(QColor(80, 200, 255, 25))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(QRectF(left_x, 0, max(0.0, right_x - left_x), floor))

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
        # Translucent tool windows don't auto-erase; wipe or frames smear.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(event.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        if _DEBUG_DRAW_COLLIDERS:
            self._paint_debug_colliders(painter)

        if self._show_timer:
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

        dragged = self.world.drag_cube
        rest = [c for c in self.world.cubes if c.alive and c is not dragged]
        if rest:
            self._paint_cubes(painter, rest)
        if dragged is not None and dragged.alive:
            self._paint_cubes(painter, [dragged])
        painter.end()

    def _paint_cubes(self, painter: QPainter, cubes: Sequence[Cube]) -> None:
        for cube in cubes:
            alpha = 220
            if cube.despawn_t is not None:
                alpha = max(0, int(220 * (cube.despawn_t / _DESPAWN_FADE_SEC)))
            dragging = cube is self.world.drag_cube
            if dragging:
                color = QColor(110, 180, 255, alpha)
                border = QColor(50, 120, 210, alpha)
            else:
                color = QColor(70, 140, 220, alpha)
                border = QColor(30, 80, 160, alpha)
            painter.setBrush(color)
            painter.setPen(border)
            radius = max(2.0, cube.size * 0.18)
            scale = 1.0
            if cube.despawn_t is not None:
                scale = max(0.15, cube.despawn_t / _DESPAWN_FADE_SEC)
            elif dragging:
                scale = 1.08
            cx = cube.x + cube.size / 2
            cy = cube.y + cube.size / 2
            s = cube.size * scale
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(math.degrees(cube.angle))
            rect = QRectF(-s / 2, -s / 2, s, s)
            painter.drawRoundedRect(rect, radius * scale, radius * scale)
            painter.restore()


class BudgetOverlayController:
    """Owns the overlay widget and keeps cube count aligned with budget chunks."""

    def __init__(self, addon_module: str) -> None:
        self._addon_module = addon_module
        self._overlay: Optional[BudgetOverlay] = None
        self._floor_poll = QTimer()
        self._floor_poll.setInterval(350)
        self._floor_poll.timeout.connect(self.refresh_floor)
        self._last_seconds: Optional[int] = None
        self._chunk = 15
        # Pixels from window bottom to the review bottom-bar top; reused on menu screens.
        self._last_floor_inset: Optional[float] = None

    @property
    def overlay(self) -> Optional[BudgetOverlay]:
        return self._overlay

    def cubes_enabled(self) -> bool:
        from .config import get_config

        return bool(get_config(self._addon_module).get("show_budget_cubes", True))

    def timer_enabled(self) -> bool:
        from .config import get_config

        return bool(get_config(self._addon_module).get("show_overlay_timer", True))

    def overlay_enabled(self) -> bool:
        return self.cubes_enabled() or self.timer_enabled()

    def _apply_cube_bounds(self) -> None:
        from .config import get_config

        if self._overlay is None:
            return
        config = get_config(self._addon_module)
        left = float(config.get("cube_bounds_left_pct", 0)) / 100.0
        right = float(config.get("cube_bounds_right_pct", 100)) / 100.0
        self._overlay.world.set_horizontal_bounds(left, right)

    def apply_settings(self) -> None:
        """Show or hide overlay pieces after settings change."""
        if not self.overlay_enabled():
            self._hide_overlay()
            _log("budget overlay hidden (cubes and timer off)")
            return
        if self._overlay is None:
            self.start()
        if self._overlay is None:
            return
        self._apply_cube_bounds()
        self._overlay.set_show_timer(self.timer_enabled())
        self._overlay.show()
        self.ensure_raised()
        if self.cubes_enabled():
            if not self._floor_poll.isActive():
                self._floor_poll.start()
            self.refresh_floor()
            # Re-stack within the (possibly updated) horizontal bounds.
            n = self._overlay.world.cube_count()
            if n > 0:
                self._overlay.hydrate_settled(n)
            _log("budget cubes enabled")
        else:
            self._clear_cubes_only()
            _log("budget cubes disabled")
        if self.timer_enabled():
            _log("overlay timer enabled")
        else:
            _log("overlay timer disabled")

    def _clear_cubes_only(self) -> None:
        self._floor_poll.stop()
        if self._overlay is None:
            return
        self._overlay.world.clear()
        self._overlay._request_repaint()

    def _hide_overlay(self) -> None:
        self._floor_poll.stop()
        if self._overlay is None:
            return
        self._overlay.world.clear()
        self._overlay.hide()
        self._overlay._request_repaint()

    def start(self) -> None:
        central = mw.centralWidget()
        if central is None:
            _log("budget overlay: no central widget yet")
            return
        if self._overlay is not None:
            return
        self._overlay = BudgetOverlay(central)
        self._overlay.set_show_timer(self.timer_enabled())
        self._apply_cube_bounds()
        if not self.overlay_enabled():
            self._overlay.hide()
        elif self.cubes_enabled():
            if not self._floor_poll.isActive():
                self._floor_poll.start()
            self.refresh_floor()
        _log("budget overlay started")

    def shutdown(self) -> None:
        self._floor_poll.stop()
        if self._overlay is not None:
            self._overlay.shutdown()
            self._overlay = None
        self._last_seconds = None
        _log("budget overlay shut down")

    def ensure_raised(self) -> None:
        if self._overlay is None or not self.overlay_enabled():
            return
        self._overlay._sync_geometry()
        self._overlay.raise_()

    def set_review_active(self, active: bool) -> None:
        if not self.overlay_enabled():
            self._hide_overlay()
            return
        if not self.cubes_enabled():
            self._clear_cubes_only()
            if self._overlay is not None:
                self._overlay.set_show_timer(self.timer_enabled())
                self._overlay.show()
            self.ensure_raised()
            return
        # Keep the floor poll running on menu screens so cubes stay above Anki chrome.
        if not self._floor_poll.isActive():
            self._floor_poll.start()
        self.refresh_floor()
        if not active and self._overlay is not None:
            self._overlay.wake_all_cubes(reason="leave_review")
        self.ensure_raised()

    def hydrate_from_budget(self, seconds: int, chunk_seconds: int, max_budget_seconds: int) -> None:
        self._chunk = max(1, int(chunk_seconds))
        self._last_seconds = int(seconds)
        if not self.overlay_enabled():
            self._hide_overlay()
            return
        if self._overlay is None:
            self.start()
        if self._overlay is None:
            return
        self._overlay.set_show_timer(self.timer_enabled())
        self._overlay.show()
        self._overlay.set_display_seconds(seconds)
        if not self.cubes_enabled():
            self._clear_cubes_only()
            return
        # Ensure floor is ready before stacking hydrate piles.
        self.refresh_floor()
        count = cube_count_for_seconds(seconds, self._chunk)
        if max_budget_seconds > 0:
            count = min(count, max_cube_count(max_budget_seconds, self._chunk))
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
        self._last_seconds = int(seconds)
        if not self.overlay_enabled():
            self._hide_overlay()
            return
        if self._overlay is None:
            self.start()
        if self._overlay is None:
            return
        self._overlay.set_show_timer(self.timer_enabled())
        self._overlay.show()
        self._overlay.set_display_seconds(seconds)
        if not self.cubes_enabled():
            self._clear_cubes_only()
            return
        target = cube_count_for_seconds(seconds, self._chunk)
        if max_budget_seconds > 0:
            target = min(target, max_cube_count(max_budget_seconds, self._chunk))
        # Keep floor pinned to the review bottom-bar height on every screen.
        self.refresh_floor()
        self._overlay.sync_to_count(target, falling=falling)

    def _measure_bottom_bar_floor_y(self) -> Optional[float]:
        """Top edge of the reviewer bottom bar, in overlay coordinates."""
        if self._overlay is None:
            return None
        reviewer = getattr(mw, "reviewer", None)
        bottom = getattr(reviewer, "bottom", None) if reviewer is not None else None
        bottom_web = getattr(bottom, "web", None) if bottom is not None else None
        if bottom_web is None:
            return None
        try:
            if int(bottom_web.height()) < 8:
                return None
            top_left = self._overlay.mapFromGlobal(bottom_web.mapToGlobal(QPoint(0, 0)))
            floor_y = float(top_left.y())
        except Exception:
            return None
        height = self._overlay.world.height
        if floor_y < 40 or floor_y > height or floor_y < height * _MIN_FLOOR_HEIGHT_FRACTION:
            if _DEBUG_DRAW_COLLIDERS:
                _log(f"floor rejected y={floor_y:.1f} h={height:.1f} (need >= {height * _MIN_FLOOR_HEIGHT_FRACTION:.1f})")
            return None
        return floor_y

    def refresh_floor(self) -> None:
        """Pin the physics floor to the flashcard bottom bar on every screen."""
        if self._overlay is None or not self.cubes_enabled():
            return
        height = self._overlay.world.height
        floor_y = self._measure_bottom_bar_floor_y()
        if floor_y is not None:
            self._last_floor_inset = max(20.0, height - floor_y)
            self._overlay.set_floor_y(floor_y)
            return
        inset = self._last_floor_inset
        if inset is None or inset < 20.0:
            # Typical Anki review bottom-bar height when we have never measured.
            inset = 64.0
        self._overlay.set_floor_y(height - inset)
