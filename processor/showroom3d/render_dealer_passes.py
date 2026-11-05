"""Step 3 of the dealer backdrop: the dealer's studio rebuilt in Blender – shadow passes.

The plate itself is the dealer's (cleaned, upscaled) image; Blender only
supplies what a photo cannot: how a car standing in that room darkens the
floor. The room is rebuilt from the geometry in ``dealer_layout.py`` (the
fixed level studio camera with lens shift, wall y = 0, floor z = 0):

- floor (the wood colour of his floor, matte – see build_studio), the white brand panel and the
  wall to its left (recess with the vertical blue LED, walnut slats), the
  black skirting with the blue LED line at its foot, the right wall section
  turned towards the camera (through the measured junction points), the
  black planter;
- lights roughly as in his image: one warm wall-washer spot above every light
  pool on the wall, soft neutral ceiling panels over the floor (his floor is
  evenly lit), the blue LED lines as emitters; a dark ceiling and neutral
  walls around/behind the camera (not visible) for the bounce light.

Renders (Cycles, CPU, deterministic seed, "Standard" view transform so that
the PNG values convert to linear light exactly):

    base.png             the empty studio (the same for every shot – one camera)
    <shot>-proxy.png     the same view with the invisible light-blocking proxy car
                         (4.85 × 1.88 × 1.46 m) at the shot's pose
    layout.json          dealer_layout.layout() as used for the renders
    check.png            (--check) the studio at 724 px with the edges of the
                         dealer image overlaid – geometry check

``dealer_backdrop.py`` turns the ratio proxy / base into ``<shot>-shadow.png``.

Usage (from the repository root, Blender 4.2 LTS)::

    blender -b --factory-startup --python-exit-code 1 -P processor/showroom3d/render_dealer_passes.py -- \
        --out /opt/ae-dealer/render [--width 1200] [--samples 64] [--shots front,rear] [--check]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dealer_layout as dl  # noqa: E402
import render_plates as rp  # noqa: E402

AE_BLUE = rp.AE_BLUE
WARM = (1.0, 0.78, 0.58)
NEUTRAL = (1.0, 0.93, 0.86)
#: Linear colours of the materials (mean colours of his image, as linear albedo).
FLOOR_ALBEDO = (0.36, 0.17, 0.075)
WALL_ALBEDO = (0.78, 0.77, 0.78)
SLAT_ALBEDO = (0.16, 0.08, 0.04)
#: Exposure (stops) of the passes: nothing on the floor clips (the shadow pass is a ratio).
EXPOSURE = -2.5


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--shots", default=",".join(dl.SHOTS))
    p.add_argument("--width", type=int, default=1200)
    p.add_argument("--samples", type=int, default=64)
    p.add_argument("--check", action="store_true", help="geometry check render (724 px) instead of the passes")
    p.add_argument("--debug-proxy", action="store_true", help="make the proxy car visible")
    p.add_argument("--save-blend", action="store_true")
    return p.parse_args(argv)


def wall_plane(name, a, b, height, material, y_offset=0.0):
    """Vertical wall from floor point a to floor point b (normal towards the camera side)."""
    a, b = Vector((a[0], a[1], 0.0)), Vector((b[0], b[1], 0.0))
    mid = (a + b) / 2
    length = (b - a).length
    angle = math.atan2(b.y - a.y, b.x - a.x)
    obj = rp.plane(name, (mid.x, mid.y + y_offset, height / 2), (length, height), (math.radians(90), 0, angle), material)
    return obj


def strip(name, a, b, z, size_z, depth, material):
    """Thin box along the floor line a → b (skirting, LED line)."""
    a, b = Vector((a[0], a[1], 0.0)), Vector((b[0], b[1], 0.0))
    mid = (a + b) / 2
    length = (b - a).length
    angle = math.atan2(b.y - a.y, b.x - a.x)
    normal = Vector((-math.sin(angle), math.cos(angle), 0.0))  # left of a → b
    # the strip sits in front of the wall, i.e. on the camera side (−normal for walls built left → right)
    centre = mid - normal * (depth / 2)
    # rp.box applies location and scale (vertices in world space, origin at 0): build it at the
    # origin, turn it about z, then move it into place
    obj = rp.box(name, (0.0, 0.0, z + size_z / 2), (length, depth, size_z), material)
    obj.rotation_euler = (0, 0, angle)
    obj.location = (centre.x, centre.y, 0.0)
    return obj


def build_studio(info: dict) -> None:
    room = info["room"]
    # matte: the shadow pass is the occlusion of the floor's DIFFUSE light – the floor's own gloss
    # (LED and light-pool streaks) is the measured reflection pass, the processor removes it under
    # the car separately; a glossy floor would leave unshadowed LED streaks in the ratio
    floor = rp.mat_color("floor", FLOOR_ALBEDO, roughness=0.9, coat=0.0)
    floor.node_tree.nodes["Principled BSDF"].inputs["Specular IOR Level"].default_value = 0.0
    wall = rp.mat_color("wall", WALL_ALBEDO, roughness=0.85)
    recess = rp.mat_color("recess", (0.42, 0.45, 0.52), roughness=0.85)
    slats = rp.mat_color("slats", SLAT_ALBEDO, roughness=0.55)
    black = rp.mat_color("skirting", (0.012, 0.012, 0.014), roughness=0.4)
    planter = rp.mat_color("planter", (0.03, 0.03, 0.032), roughness=0.5)
    dark = rp.mat_color("dark", (0.02, 0.02, 0.02), roughness=0.9)
    neutral = rp.mat_color("neutral", (0.35, 0.34, 0.33), roughness=0.9)
    led = rp.mat_emission("led", AE_BLUE, 6.0)

    rp.plane("floor", (0, -10, 0), (30, 20), (0, 0, 0), floor)
    x0, x1 = room["panelX"]
    corner = Vector((room["rightWall"]["corner"][0], 0.0, 0.0))
    direction = Vector((*room["rightWall"]["direction"], 0.0))
    right_end = corner + direction * 6.0
    height = 4.6
    # main wall: slats | recess with LED | white panel | corner
    wall_plane("slats", (-12.0, 0.0), (room["slatsRightX"], 0.0), height, slats)
    wall_plane("recess", (room["slatsRightX"], 0.0), (x0, 0.0), height, recess)
    wall_plane("panel", (x0, 0.0), (corner.x, 0.0), height, wall)
    wall_plane("right_wall", (corner.x, corner.y), (right_end.x, right_end.y), height, wall)
    # vertical LEDs: left of the panel (recess) and at the right corner
    for name, x in (("led_left", room["leftLedX"]), ("led_corner", corner.x - 0.03)):
        rp.box(name, (x, -0.02, height / 2), (0.03, 0.02, height), led)
    # skirting + LED line at its foot along both walls
    skirt = room["skirtingHeightM"]
    strip("skirting_main", (-12.0, 0.0), (corner.x, 0.0), 0.0, skirt, 0.03, black)
    strip("skirting_right", (corner.x, corner.y), (right_end.x, right_end.y), 0.0, skirt, 0.03, black)
    strip("led_main", (-12.0, -0.03), (corner.x, -0.03), 0.0, 0.012, 0.012, led)
    strip("led_right", (corner.x, corner.y), (right_end.x, right_end.y), 0.0, 0.012, 0.042, led)
    # planter (front face measured), 0.75 m deep towards the wall, its left side out of the frame
    pl = room["planter"]
    rp.box("planter", (pl["rightX"] - 0.6, pl["frontY"] + 0.375, pl["topZ"] / 2), (1.2, 0.75, pl["topZ"]), planter)
    # closed room around the camera (bounce light), dark ceiling
    rp.plane("ceiling", (0, -8, height), (30, 20), (math.radians(180), 0, 0), dark)
    rp.plane("back", (0, -16, height / 2), (30, height), (math.radians(90), 0, math.radians(180)), neutral)
    rp.plane("side_l", (-9, -8, height / 2), (16, height), (math.radians(90), 0, math.radians(90)), neutral)
    rp.plane("side_r", (9.5, -8, height / 2), (16, height), (math.radians(90), 0, math.radians(-90)), neutral)

    # wall washers: one warm spot above every light pool, aimed at the wall below it
    for i, (px, py) in enumerate(room["pools"]):
        if py == 0.0:
            pos = Vector((px, -0.55, 4.1))
            target = Vector((px, 0.0, 1.6))
        else:  # right wall: in front of it, along its normal
            normal = Vector((-direction.y, direction.x, 0.0))  # towards the room
            if normal.y > 0:
                normal = -normal
            pos = Vector((px, py, 4.1)) + normal * 0.55
            target = Vector((px, py, 1.6))
        rp.light(f"washer_{i}", "SPOT", pos, 900.0, WARM, direction=target - pos, size=0.08, spot=58.0, blend=0.6)
    # soft ceiling panels over the floor (the even floor light of his image)
    for ix, x in enumerate((-4.0, -1.3, 1.3, 4.0)):
        for iy, y in enumerate((-1.6, -3.8, -6.0, -8.2)):
            rp.light(f"panel_{ix}{iy}", "AREA", (x, y, 4.15), 220.0, NEUTRAL, direction=(0, 0, -1), size=1.2)


def make_camera():
    data = bpy.data.cameras.new("studio_cam")
    data.sensor_fit = "HORIZONTAL"
    data.sensor_width = rp.SENSOR_W
    data.lens = rp.SENSOR_W * dl.FOCAL_NORM
    data.shift_x = 0.0
    data.shift_y = dl.SHIFT_Y
    data.clip_start = 0.05
    data.clip_end = 60
    cam = bpy.data.objects.new("studio_cam", data)
    bpy.context.collection.objects.link(cam)
    cam.location = Vector(dl.camera_position())
    cam.rotation_euler = (math.radians(90), 0.0, 0.0)  # level, looking along +y
    bpy.context.scene.camera = cam
    bpy.context.view_layer.update()
    return cam


def check_projection(cam) -> float:
    """Largest difference (fraction of the frame) between Blender's projection and dealer_layout.project."""
    from bpy_extras.object_utils import world_to_camera_view

    scene = bpy.context.scene
    worst = 0.0
    for p in ((0.0, 0.0, 0.0), (3.0, 0.0, 2.5), (-2.0, -3.0, 0.0), (1.0, -5.0, 1.4), (-4.0, 0.0, 3.5)):
        co = world_to_camera_view(scene, cam, Vector(p))
        u, v = co.x, 1.0 - co.y
        pu, pv, _ = dl.project(p)
        worst = max(worst, abs(u - float(pu)), abs(v - float(pv)))
    return worst


def setup(width: int, samples: int) -> None:
    rp.setup_render(width, samples)
    scene = bpy.context.scene
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = EXPOSURE
    scene.cycles.seed = 7
    scene.cycles.use_animated_seed = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 3


def place_proxy(shot: dict, visible: bool):
    rp.CAR_L, rp.CAR_W, rp.CAR_H = dl.CAR["lengthM"], dl.CAR["widthM"], dl.CAR["heightM"]
    rp.WHEELBASE, rp.TRACK, rp.WHEEL_R = dl.CAR["wheelbaseM"], dl.CAR["trackM"], dl.WHEEL_R
    root, parts = rp.build_proxy(shot["headingDeg"], visible=visible)
    root.location = Vector((shot["anchor"][0], shot["anchor"][1], 0.0))
    bpy.context.view_layer.update()
    return root, parts


def main() -> None:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    info = dl.layout()
    width = 724 if args.check else args.width
    setup(width, 24 if args.check else args.samples)
    build_studio(info)
    cam = make_camera()
    error = check_projection(cam)
    if error > 1e-4:
        raise SystemExit(f"Blender camera and dealer_layout.project disagree by {error:.5f}")
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "layout.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    if args.check:
        shot = info["shots"]["front_left_45"]
        root, parts = place_proxy(shot, visible=True)
        rp.render_to(os.path.join(args.out, "check-render.png"))
        return
    rp.render_to(os.path.join(args.out, "base.png"))
    for name in [s for s in args.shots.split(",") if s]:
        root, parts = place_proxy(info["shots"][name], visible=args.debug_proxy)
        rp.render_to(os.path.join(args.out, f"{name}-proxy.png"))
        for p in parts:
            bpy.data.objects.remove(p, do_unlink=True)
        bpy.data.objects.remove(root, do_unlink=True)
    if args.save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(args.out, "studio.blend"))


if __name__ == "__main__":
    main()
