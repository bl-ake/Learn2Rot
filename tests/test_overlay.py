# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def test_cube_count_for_seconds() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    assert overlay.cube_count_for_seconds(0, 15) == 0
    assert overlay.cube_count_for_seconds(14, 15) == 0
    assert overlay.cube_count_for_seconds(15, 15) == 1
    assert overlay.cube_count_for_seconds(44, 15) == 2
    assert overlay.cube_count_for_seconds(45, 15) == 3


def test_max_cube_count() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    assert overlay.max_cube_count(600, 15) == 40
    assert overlay.max_cube_count(10, 15) == 0


def test_cubes_removed_crossing() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    assert overlay.cubes_removed_crossing(30, 29, 15) == 1  # 2 cubes -> 1
    assert overlay.cubes_removed_crossing(29, 16, 15) == 0  # still 1 cube
    assert overlay.cubes_removed_crossing(30, 15, 15) == 1
    assert overlay.cubes_removed_crossing(30, 0, 15) == 2
    assert overlay.cubes_removed_crossing(10, 20, 15) == 0


def test_resolve_aabb_penetration_top() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    side = overlay.resolve_aabb_penetration(
        (10, 40, 20, 20),
        (0, 50, 100, 80),
    )
    assert side == "top"


def test_normalize_rejects_spawn_band() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    assert (
        overlay.normalize_card_platform(0, 10, 200, 40, vw=400, vh=600) is None
    )


def test_normalize_clamps_wide_platform() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    platform = overlay.normalize_card_platform(
        0, 100, 400, 40, vw=400, vh=600
    )
    assert platform is not None
    _x, _y, w, _h = platform
    assert w <= 400 * overlay._MAX_PLATFORM_VIEW_WIDTH + 0.1


def test_physics_world_spawn_and_despawn() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    world = overlay.PhysicsWorld(width=200, height=300)
    world.spawn_settled_pile(3)
    assert world.cube_count() == 3
    world.begin_despawn_random(1)
    assert world.cube_count() == 2
    world.step(overlay._DESPAWN_FADE_SEC + 0.1)
    assert len(world.cubes) == 2


def test_physics_settles_on_custom_floor() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    world = overlay.PhysicsWorld(width=200, height=300, floor_y=250)
    cube = world.spawn_falling()
    assert (
        abs(cube.x + cube.size / 2 - world.width * overlay._SPAWN_X_FRACTION)
        < overlay._SPAWN_X_JITTER + 1
    )
    assert cube.omega != 0
    cube.x = 50
    cube.y = 200
    cube.vx = 0
    cube.vy = 10
    for _ in range(180):
        world.step()
    alive = world.alive_cubes()
    assert alive
    assert alive[0].settled is True
    # Segment radius offsets contact slightly above floor_y.
    assert abs(alive[0].y + alive[0].size - 250) < 3.0
    # Pymunk may leave a tiny residual spin on sleeping bodies.
    assert abs(alive[0].omega) < 0.05


def test_one_way_platform_does_not_trap_at_top() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    world = overlay.PhysicsWorld(width=400, height=600, floor_y=600)
    # Wide thin platform near mid-upper area
    world.colliders = [(50, 120, 300, 30)]
    cube = world.spawn_falling()
    cube.x = 180
    cube.y = 80
    cube.vx = 0
    cube.vy = 200
    for _ in range(300):
        world.step()
    alive = world.alive_cubes()
    assert alive
    # Must reach the floor rather than remaining stuck on the platform forever.
    assert alive[0].settled is True
    assert alive[0].y + alive[0].size >= world.floor_y - 2


def test_wake_cubes_not_on_floor() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    world = overlay.PhysicsWorld(width=200, height=400, floor_y=400)
    world.spawn_settled_pile(1)
    # Fake a cube settled near the top (bad early floor).
    stuck = world.alive_cubes()[0]
    stuck.y = 20
    stuck.settled = True
    world.floor_y = 350
    woken = world.wake_cubes_not_on_floor(reason="test")
    assert woken == 1
    assert stuck.settled is False


def test_rehome_lifts_pile_when_floor_rises() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    world = overlay.PhysicsWorld(width=200, height=400, floor_y=400)
    world.spawn_settled_pile(3)
    before = [(c.x, c.y + c.size) for c in world.alive_cubes()]
    old_floor = world.floor_y
    world.floor_y = 340
    world.update_boundaries()
    moved = world.rehome_for_floor_change(old_floor, reason="test")
    assert moved == 3
    lift = old_floor - world.floor_y
    for (x0, bottom0), cube in zip(before, world.alive_cubes()):
        assert cube.settled is True
        assert abs((cube.y + cube.size) - (bottom0 - lift)) < 0.5
        assert abs(cube.x - x0) < 0.5
        assert cube.y + cube.size <= world.floor_y + 1.0


def test_rehome_does_not_move_midair_cubes() -> None:
    overlay = load_addon_module("overlay", "overlay.py")
    world = overlay.PhysicsWorld(width=200, height=400, floor_y=400)
    world.spawn_settled_pile(1)
    floating = world.alive_cubes()[0]
    floating.y = 80
    floating.settled = True
    old_y = floating.y
    old_floor = world.floor_y
    world.floor_y = 340
    world.update_boundaries()
    world.rehome_for_floor_change(old_floor, reason="test")
    assert floating.y == old_y
