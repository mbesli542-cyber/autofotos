"""AutoExperten Standard showroom – one physical 3D room, one camera per exterior shot.

The room is modelled after the approved showroom design
(public/presets/autoexperten-standard-reference.jpg): white brand wall with warm
wall-wash spots and floor up-lights, walnut slat panels with vertical blue LED
strips, olive trees in black planters, black ceiling tracks and a lacquered oak
plank floor. Only deterministic 3D rendering (Blender Cycles) with CC0 assets
from Poly Haven – no generative AI, no stock dealership photos.

Every exterior shot gets its own camera (position, height, focal length,
direction), so the eight plates show the SAME room from different, shot
appropriate positions. For every plate this script also writes the metadata the
processor needs (camera, horizon, floor/wall homographies, where a typical car
stands, proxy tyre contacts) and renders a second pass with an invisible car
proxy that only blocks light: the ratio of the two renders is the physically lit
shadow/occlusion of a car standing at that spot (``<shot>-shadow.png``).
The optional ``reflection`` pass renders the same low-resolution view with a
gloss-free floor (lacquer coat and specular switched off): the difference to
the shadow-pass base render is the floor's own reflection of the LEDs and the
wall (``<shot>-reflection.png``), which the processor removes where a real car
blocks it.

The wall is rendered EMPTY. The official logo and texts are composited by the
processor (app/showroom/branding.py) through the wall homography.

Usage (from the repository root, Blender 4.2 LTS):

    blender -b --factory-startup -P processor/showroom3d/render_plates.py -- \
        --assets /opt/showroom-assets --out public/presets/autoexperten-standard

    options: --shots front_left_45,left_side  --width 3200  --samples 160
             --pass plate,shadow,reflection (reflection: gloss-free floor at --shadow-width)
             --preview (800 px, 24 samples)   --no-shadow   --reference-view
Assets: processor/showroom3d/fetch_assets.py downloads the CC0 files.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

# --------------------------------------------------------------------------
# Room (metres). Z up. Brand wall in the plane y = 0, facing -y (towards the cameras).
# --------------------------------------------------------------------------
ROOM_HALF_W = 8.0  # x in [-8, 8]
ROOM_DEPTH = 14.0  # y in [-14, 0]
ROOM_H = 4.2
WALL_ALBEDO = (0.60, 0.585, 0.56)  # linear – warm white paint (matches branding light matching)
SLAT_INNER, SLAT_OUTER = 3.35, 4.75  # |x| range of the walnut slat panels
LED_X = 3.9  # |x| of the blue LED strip inside each slat panel
LED_CORNER_X = 7.93  # |x| of the corner LED strips
PLANTER_X, PLANTER_Y = 4.25, -0.78
SPOT_XS = (-2.45, -0.82, 0.82, 2.45)  # wall-wash spots
UPLIGHT_XS = (-1.65, 1.65, -3.15, 3.15)
TRACK_XS = (-2.4, 2.4)
AE_BLUE = (0.003, 0.198, 1.0)  # #0A7BFF in linear sRGB
WARM_3000K = (1.0, 0.66, 0.38)
NEUTRAL_4000K = (1.0, 0.89, 0.80)

# Where the car stands (centre of its footprint on the floor).
ANCHOR = Vector((0.0, -3.1, 0.0))
# Typical car used to design the cameras and the shadow proxy.
CAR_L, CAR_W, CAR_H = 4.75, 1.88, 1.46
WHEELBASE, TRACK, WHEEL_R = 2.86, 1.60, 0.335

# Shot cameras. heading = direction the car's FRONT points (deg, 0 = +x, CCW),
# derived from the camera direction so the camera sees the required corner/side.
# orbit = camera azimuth around the anchor (deg, 0 = straight in front of the
# wall, + = camera moved to the right), dist = horizontal camera-to-anchor
# distance, height = camera height (phone at chest height). The camera direction is
# solved so that a typical car standing at the anchor is centred horizontally and
# its lowest tyre contact sits at ground_v (fraction of the image height).
SHOTS = {
    # the wall recedes on the same side as the car's long side → camera on that side
    "front_left_45": dict(view="front_left", orbit=-14.0, dist=4.55, height=1.35, ground_v=0.86, target_width=0.81),
    "front_right_45": dict(view="front_right", orbit=14.0, dist=4.55, height=1.35, ground_v=0.86, target_width=0.81),
    "rear_left_45": dict(view="rear_left", orbit=10.0, dist=4.55, height=1.4, ground_v=0.86, target_width=0.81),
    "rear_right_45": dict(view="rear_right", orbit=-10.0, dist=4.55, height=1.4, ground_v=0.86, target_width=0.81),
    "left_side": dict(view="left", orbit=-3.0, dist=5.25, height=1.3, ground_v=0.84, target_width=0.85),
    "right_side": dict(view="right", orbit=3.0, dist=5.25, height=1.3, ground_v=0.84, target_width=0.85),
    "front": dict(view="front", orbit=-2.0, dist=4.7, height=1.25, ground_v=0.84, target_width=0.62),
    "rear": dict(view="rear", orbit=2.0, dist=4.7, height=1.3, ground_v=0.84, target_width=0.62),
}
# Angle between the car's heading and the direction car→camera, per view.
VIEW_ANGLES = {
    "front": 0.0,
    "front_left": 45.0,
    "left": 90.0,
    "rear_left": 135.0,
    "rear": 180.0,
    "rear_right": -135.0,
    "right": -90.0,
    "front_right": -45.0,
}
HFOV_DEG = 66.0  # ≈ smartphone main camera in 4:3 (26 mm equiv.)
FLOOR_COAT = 0.45  # lacquer coat weight of the oak floor
SENSOR_W = 36.0


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--assets", default=os.environ.get("SHOWROOM_ASSETS", "/opt/showroom-assets"))
    p.add_argument("--out", required=True)
    p.add_argument("--shots", default=",".join(SHOTS))
    p.add_argument("--width", type=int, default=3200)
    p.add_argument("--samples", type=int, default=160)
    p.add_argument("--preview", action="store_true")
    p.add_argument("--no-shadow", action="store_true")
    p.add_argument("--pass", dest="passes", default="plate,shadow", help="plate,shadow,reflection")
    p.add_argument("--shadow-width", type=int, default=1200)
    p.add_argument("--shadow-samples", type=int, default=48)
    p.add_argument("--reference-view", action="store_true", help="render the straight-on design view only")
    p.add_argument("--save-blend", action="store_true")
    p.add_argument("--debug-proxy", action="store_true", help="make the car proxy visible (placement check)")
    return p.parse_args(argv)


# --------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------
def _principled(name: str):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    return mat, nodes, mat.node_tree.links, bsdf


def _tex(nodes, links, path: str, mapping, non_color: bool = False):
    node = nodes.new("ShaderNodeTexImage")
    node.image = bpy.data.images.load(path, check_existing=True)
    if non_color:
        node.image.colorspace_settings.name = "Non-Color"
    links.new(mapping.outputs["Vector"], node.inputs["Vector"])
    return node


def _mapping(nodes, links, scale=(1.0, 1.0, 1.0), coords="Object"):
    coord = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = scale
    links.new(coord.outputs[coords], mapping.inputs["Vector"])
    return mapping


def mat_color(name, color, roughness=0.5, metallic=0.0, coat=0.0):
    mat, _nodes, _links, bsdf = _principled(name)
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Coat Weight"].default_value = coat
    return mat


def mat_emission(name, color, strength):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    em = nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1.0)
    em.inputs["Strength"].default_value = strength
    links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def mat_floor(assets: str):
    mat, nodes, links, bsdf = _principled("oak_floor")
    base = os.path.join(assets, "laminate_floor_02")
    # world-space mapping: the texture shows 8 planks (≈ 0.19 m each) along U → planks run along x
    mapping = _mapping(nodes, links, scale=(1 / 1.55, 1 / 1.55, 1.0))
    diff = _tex(nodes, links, f"{base}/laminate_floor_02_diff_4k.jpg", mapping)
    rough = _tex(nodes, links, f"{base}/laminate_floor_02_rough_4k.jpg", mapping, non_color=True)
    nor = _tex(nodes, links, f"{base}/laminate_floor_02_nor_gl_4k.jpg", mapping, non_color=True)
    # slightly warmer, a touch lighter oak than the texture (matches the design)
    hsv = nodes.new("ShaderNodeHueSaturation")
    hsv.inputs["Hue"].default_value = 0.5
    hsv.inputs["Saturation"].default_value = 1.18
    hsv.inputs["Value"].default_value = 0.72
    links.new(diff.outputs["Color"], hsv.inputs["Color"])
    links.new(hsv.outputs["Color"], bsdf.inputs["Base Color"])
    rmap = nodes.new("ShaderNodeMapRange")
    rmap.inputs["To Min"].default_value = 0.38
    rmap.inputs["To Max"].default_value = 0.62
    links.new(rough.outputs["Color"], rmap.inputs["Value"])
    links.new(rmap.outputs["Result"], bsdf.inputs["Roughness"])
    nmap = nodes.new("ShaderNodeNormalMap")
    nmap.inputs["Strength"].default_value = 0.5
    links.new(nor.outputs["Color"], nmap.inputs["Color"])
    links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    # lacquer: the glossy reflections of the LED strips and the wall
    bsdf.inputs["Coat Weight"].default_value = FLOOR_COAT
    bsdf.inputs["Coat Roughness"].default_value = 0.09
    return mat


def mat_walnut(assets: str):
    mat, nodes, links, bsdf = _principled("walnut_slats")
    base = os.path.join(assets, "oak_veneer_01")
    mapping = _mapping(nodes, links, scale=(1.0, 1.0, 0.45))
    diff = _tex(nodes, links, f"{base}/oak_veneer_01_diff_2k.jpg", mapping)
    rough = _tex(nodes, links, f"{base}/oak_veneer_01_rough_2k.jpg", mapping, non_color=True)
    # oak veneer → warm walnut tone
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.inputs["Factor"].default_value = 1.0
    mix.inputs["B"].default_value = (0.48, 0.30, 0.17, 1.0)
    links.new(diff.outputs["Color"], mix.inputs["A"])
    links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
    rmap = nodes.new("ShaderNodeMapRange")
    rmap.inputs["To Min"].default_value = 0.45
    rmap.inputs["To Max"].default_value = 0.7
    links.new(rough.outputs["Color"], rmap.inputs["Value"])
    links.new(rmap.outputs["Result"], bsdf.inputs["Roughness"])
    return mat


def mat_wall(assets: str):
    mat, nodes, links, bsdf = _principled("brand_wall")
    base = os.path.join(assets, "painted_plaster_wall")
    mapping = _mapping(nodes, links, scale=(1 / 2.2, 1 / 2.2, 1 / 2.2))
    nor = _tex(nodes, links, f"{base}/painted_plaster_wall_nor_gl_2k.jpg", mapping, non_color=True)
    bsdf.inputs["Base Color"].default_value = (*WALL_ALBEDO, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.82
    nmap = nodes.new("ShaderNodeNormalMap")
    nmap.inputs["Strength"].default_value = 0.08
    links.new(nor.outputs["Color"], nmap.inputs["Color"])
    links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------
def box(name, center, size, material, bevel: float = 0.0):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=center)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(scale=True)
    if bevel > 0:
        mod = obj.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
    obj.data.materials.append(material)
    return obj


def plane(name, center, size, rotation, material):
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=center, rotation=rotation)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size[0], size[1], 1.0)
    bpy.ops.object.transform_apply(scale=True)
    obj.data.materials.append(material)
    return obj


def cylinder(name, center, radius, depth, material, rotation=(0, 0, 0), vertices=48):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, location=center, rotation=rotation, vertices=vertices)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.append(material)
    bpy.ops.object.shade_smooth()
    return obj


def light(name, kind, location, energy, color, *, direction=None, size=None, size_y=None, spot=None, blend=0.3):
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    data.color = color
    if kind == "AREA":
        data.shape = "RECTANGLE"
        data.size = size
        data.size_y = size_y or size
    if kind == "SPOT":
        data.spot_size = math.radians(spot)
        data.spot_blend = blend
        data.shadow_soft_size = size or 0.05
    if kind == "POINT":
        data.shadow_soft_size = size or 0.05
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    if direction is not None:
        obj.rotation_euler = Vector(direction).to_track_quat("-Z", "Y").to_euler()
    if kind == "AREA":
        # large fills must not show up as bright rectangles in the lacquered floor
        obj.visible_glossy = False
    return obj


# --------------------------------------------------------------------------
# Room
# --------------------------------------------------------------------------
def build_room(assets: str) -> dict:
    wall = mat_wall(assets)
    white = mat_color("ceiling_white", (0.55, 0.55, 0.54), roughness=0.92)
    floor = mat_floor(assets)
    walnut = mat_walnut(assets)
    black = mat_color("black_metal", (0.012, 0.012, 0.013), roughness=0.38, metallic=0.6)
    felt = mat_color("black_felt", (0.008, 0.007, 0.007), roughness=0.95)
    planter_mat = mat_color("planter_black", (0.015, 0.015, 0.016), roughness=0.32, coat=0.4)
    led = mat_emission("led_blue", AE_BLUE, 1.1)
    led_warm = mat_emission("led_warm", WARM_3000K, 60.0)
    downlight = mat_emission("downlight", NEUTRAL_4000K, 40.0)

    hw, d, h = ROOM_HALF_W, ROOM_DEPTH, ROOM_H
    objs = {}
    objs["floor"] = plane("floor", (0, -d / 2, 0), (2 * hw, d), (0, 0, 0), floor)
    objs["wall"] = plane("brand_wall", (0, 0, h / 2), (2 * hw, h), (math.radians(90), 0, 0), wall)
    plane("ceiling", (0, -d / 2, h), (2 * hw, d), (math.radians(180), 0, 0), white)
    plane("side_wall_l", (-hw, -d / 2, h / 2), (d, h), (math.radians(90), 0, math.radians(90)), wall)
    plane("side_wall_r", (hw, -d / 2, h / 2), (d, h), (math.radians(90), 0, math.radians(-90)), wall)
    plane("front_wall", (0, -d, h / 2), (2 * hw, h), (math.radians(90), 0, 0), wall)

    # baseboard (black shadow gap) along the brand wall
    box("baseboard", (0, -0.008, 0.035), (2 * hw, 0.016, 0.07), black)

    # walnut slat panels: dark felt backing + vertical slats + blue LED strip
    for side in (-1, 1):
        x0, x1 = side * SLAT_INNER, side * SLAT_OUTER
        cx, width = (x0 + x1) / 2, abs(x1 - x0)
        box(f"felt_{side}", (cx, -0.012, h / 2), (width, 0.024, h), felt)
        slat_w, gap = 0.032, 0.022
        n = int(width / (slat_w + gap))
        start = min(x0, x1) + (width - (n * (slat_w + gap) - gap)) / 2 + slat_w / 2
        for i in range(n):
            x = start + i * (slat_w + gap)
            if abs(abs(x) - LED_X) < 0.035:
                continue  # leave the slot for the LED strip
            box(f"slat_{side}_{i}", (x, -0.045, h / 2), (slat_w, 0.045, h), walnut, bevel=0.002)
        box(f"led_{side}", (side * LED_X, -0.03, 0.2 + (h - 0.75) / 2), (0.04, 0.012, h - 0.75), led)
        light(f"led_glow_{side}", "AREA", (side * LED_X, -0.045, 0.2 + (h - 0.75) / 2), 40.0, AE_BLUE, direction=(0, -1, 0), size=0.04, size_y=h - 0.75)
        light(f"led_corner_glow_{side}", "AREA", (side * (LED_CORNER_X - 0.02), -0.08, h / 2), 22.0, AE_BLUE, direction=(-side, -0.6, 0), size=0.04, size_y=h - 0.3)
        box(f"led_corner_{side}", (side * LED_CORNER_X, -0.06, h / 2), (0.022, 0.012, h - 0.3), led)

    # ceiling: two black track channels running towards the cameras + track spots
    for x in TRACK_XS:
        box(f"track_{x}", (x, -d / 2, h - 0.025), (0.06, d, 0.05), black)
    # wall-wash spots (black cylinders) and their warm light cones
    for i, x in enumerate(SPOT_XS):
        y = -0.85
        cylinder(f"spot_can_{i}", (x, y, h - 0.16), 0.055, 0.22, black)
        cylinder(f"spot_lens_{i}", (x, y, h - 0.271), 0.045, 0.004, led_warm)
        light(f"spot_{i}", "SPOT", (x, y, h - 0.28), 540.0, WARM_3000K, direction=(0, 0.75, -1.0), spot=62, blend=0.9, size=0.03)
    # floor up-lights at the wall base
    for i, x in enumerate(UPLIGHT_XS):
        cylinder(f"uplight_{i}", (x, -0.12, 0.001), 0.035, 0.004, led_warm)
        light(f"uplight_l_{i}", "SPOT", (x, -0.12, 0.02), 55.0, WARM_3000K, direction=(0, 0.18, 1.0), spot=34, blend=0.7, size=0.02)
    # recessed downlights over the room (visible as small discs, light via area lights)
    for i, (x, y) in enumerate([(sx, sy) for sx in (-5.5, -1.8, 1.8, 5.5) for sy in (-2.5, -5.5, -8.5, -11.5)]):
        cylinder(f"downlight_{i}", (x, y, h - 0.002), 0.06, 0.003, downlight)
    # soft studio light over the car position + general fill
    light("car_softbox", "AREA", (ANCHOR.x, ANCHOR.y, h - 0.05), 300.0, NEUTRAL_4000K, direction=(0, 0, -1), size=8.0, size_y=4.6)
    for side in (-1, 1):  # side fills soften the shadow under the car
        light(f"car_side_{side}", "AREA", (ANCHOR.x + side * 4.6, ANCHOR.y - 0.6, h - 0.4), 90.0, NEUTRAL_4000K, direction=(-side * 0.8, 0.1, -1), size=2.5, size_y=4.0)
    light("fill_front", "AREA", (0, -8.5, h - 0.05), 60.0, NEUTRAL_4000K, direction=(0, 0, -1), size=9.0, size_y=4.0)
    light("fill_wall", "AREA", (0, -1.2, h - 0.05), 8.0, WARM_3000K, direction=(0, 0.35, -1), size=12.0, size_y=0.6)

    # olive trees in black planters
    for side in (-1, 1):
        tapered_planter(f"planter_{side}", (side * PLANTER_X, PLANTER_Y), top=0.58, bottom=0.44, height=0.74, material=planter_mat)
        soil = mat_color(f"soil_{side}", (0.03, 0.022, 0.016), roughness=1.0)
        box(f"soil_{side}", (side * PLANTER_X, PLANTER_Y, 0.71), (0.54, 0.54, 0.02), soil)
        tree = import_tree(assets, f"olive_{side}")
        if tree is not None:
            tree.location = (side * PLANTER_X, PLANTER_Y, 0.70)
            tree.rotation_euler = (0, 0, math.radians(35 if side < 0 else 200))
    return objs


def tapered_planter(name, xy, *, top, bottom, height, material):
    """Square planter, wider at the top (like the design's black planters)."""
    import bmesh

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    lo = [bm.verts.new((sx * bottom / 2, sy * bottom / 2, 0.0)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    hi = [bm.verts.new((sx * top / 2, sy * top / 2, height)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    bm.faces.new(lo[::-1])
    bm.faces.new(hi)
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.location = (xy[0], xy[1], 0.0)
    obj.data.materials.append(material)
    bevel = obj.modifiers.new("bevel", "BEVEL")
    bevel.width = 0.012
    bevel.segments = 3
    return obj


def import_tree(assets: str, name: str):
    path = os.path.join(assets, "island_tree_02", "island_tree_02_2k.gltf")
    if not os.path.isfile(path):
        print(f"[showroom] tree asset missing: {path}")
        return None
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    root = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(root)
    for obj in new:
        if obj.parent is None:
            obj.parent = root
    # the asset is ≈ 2.6 m tall → ≈ 1.55 m above the planter rim
    root.scale = (0.52, 0.52, 0.52)
    return root


# --------------------------------------------------------------------------
# Car proxy (casts shadow / occlusion only – never visible, never reflected)
# --------------------------------------------------------------------------
def build_proxy(heading_deg: float, visible: bool = False):
    mat = mat_color("proxy", (0.05, 0.05, 0.05), roughness=0.5)
    parts = []
    # lower body (rounded), cabin, four wheels
    parts.append(box("proxy_body", (0, 0, 0.2 + 0.56 / 2), (CAR_L, CAR_W, 0.56), mat, bevel=0.14))
    parts.append(box("proxy_cabin", (-0.15, 0, 0.75 + (CAR_H - 0.75) / 2), (2.7, CAR_W - 0.24, CAR_H - 0.75), mat, bevel=0.18))
    for sx in (-1, 1):
        for sy in (-1, 1):
            parts.append(
                cylinder(
                    f"proxy_wheel_{sx}{sy}",
                    (sx * WHEELBASE / 2, sy * (TRACK / 2), WHEEL_R),
                    WHEEL_R,
                    0.23,
                    mat,
                    rotation=(math.radians(90), 0, 0),
                )
            )
    root = bpy.data.objects.new("car_proxy", None)
    bpy.context.collection.objects.link(root)
    for p in parts:
        p.parent = root
        p.visible_camera = visible
        p.visible_glossy = False
        p.visible_transmission = False
        p.visible_volume_scatter = False
        p.visible_diffuse = True
        p.visible_shadow = True
    root.location = ANCHOR
    root.rotation_euler = (0, 0, math.radians(heading_deg))
    return root, parts


# --------------------------------------------------------------------------
# Cameras and metadata
# --------------------------------------------------------------------------
def shot_camera(shot: dict) -> tuple[Vector, Vector, float]:
    """Camera position, look-at target and the car heading for a shot."""
    a = math.radians(shot["orbit"])
    direction = Vector((math.sin(a), -math.cos(a), 0.0))  # anchor → camera
    position = ANCHOR + direction * shot["dist"] + Vector((0, 0, shot["height"]))
    target = Vector((ANCHOR.x, ANCHOR.y, 0.6))
    to_cam_deg = math.degrees(math.atan2(direction.y, direction.x))
    heading = to_cam_deg - VIEW_ANGLES[shot["view"]]
    return position, target, heading


def make_camera(position: Vector, target: Vector):
    data = bpy.data.cameras.new("cam")
    data.sensor_fit = "HORIZONTAL"
    data.sensor_width = SENSOR_W
    data.lens = SENSOR_W / (2 * math.tan(math.radians(HFOV_DEG) / 2))
    data.clip_start = 0.05
    data.clip_end = 100
    cam = bpy.data.objects.new("cam", data)
    bpy.context.collection.objects.link(cam)
    cam.location = position
    cam.rotation_euler = (target - position).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    bpy.context.view_layer.update()
    return cam


def projector(cam, width: int, height: int):
    """World point → normalised image coordinates (u right, v down, 0..1)."""
    f_px = cam.data.lens / cam.data.sensor_width * width
    world_to_cam = cam.matrix_world.inverted()

    def project(p: Vector) -> tuple[float, float, float]:
        c = world_to_cam @ Vector(p)
        z = -c.z  # camera looks along -Z
        u = (f_px * c.x / z + width / 2) / width
        v = (height / 2 - f_px * c.y / z) / height
        return u, v, z

    return project, f_px


def homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> list[list[float]]:
    """3×3 homography from 4 point pairs (DLT, normalised so H[2][2] = 1)."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.append(v)
    # tiny Gaussian elimination (8×8)
    n = 8
    m = [r + [b] for r, b in zip(rows, rhs)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                factor = m[r][col] / m[col][col]
                m[r] = [a - factor * b for a, b in zip(m[r], m[col])]
    h = [m[i][n] / m[i][i] for i in range(n)] + [1.0]
    return [h[0:3], h[3:6], h[6:9]]


def solve_camera(cam, shot: dict, heading: float, width: int, height: int) -> list[float]:
    """Turn the camera (yaw/pitch only, position fixed) so the proxy car is centred
    horizontally and its lowest tyre contact lands on ground_v."""
    root, parts = build_proxy(heading)
    bpy.context.view_layer.update()
    rot = Matrix.Rotation(math.radians(heading), 3, "Z")
    contacts = [ANCHOR + rot @ Vector((sx * WHEELBASE / 2, sy * TRACK / 2, 0.0)) for sx in (-1, 1) for sy in (-1, 1)]
    for _ in range(6):
        bbox = proxy_bbox(parts, cam, width, height)
        project, f_px = projector(cam, width, height)
        lowest_v = max(project(c)[1] for c in contacts)
        du = (bbox[0] + bbox[2]) / 2 - 0.5
        dv = lowest_v - shot["ground_v"]
        yaw = math.atan(du * width / f_px)
        pitch = math.atan(dv * height / f_px)
        # yaw about the world vertical, pitch about the camera's own x axis
        world = cam.matrix_world.to_3x3()
        r_yaw = Matrix.Rotation(-yaw, 3, "Z")
        r_pitch = Matrix.Rotation(-pitch, 3, world @ Vector((1, 0, 0)))
        new = r_yaw @ r_pitch @ world
        cam.matrix_world = Matrix.Translation(cam.location) @ new.to_4x4()
        bpy.context.view_layer.update()
    bbox = proxy_bbox(parts, cam, width, height)
    for p in parts:
        bpy.data.objects.remove(p, do_unlink=True)
    bpy.data.objects.remove(root, do_unlink=True)
    return bbox


def proxy_bbox(parts, cam, width: int, height: int) -> list[float]:
    """Projected bounding box of the proxy mesh (normalised u0, v0, u1, v1)."""
    project, _ = projector(cam, width, height)
    deps = bpy.context.evaluated_depsgraph_get()
    us, vs = [], []
    for part in parts:
        ev = part.evaluated_get(deps)
        mesh = ev.to_mesh()
        for vert in mesh.vertices:
            u, v, _ = project(ev.matrix_world @ vert.co)
            us.append(u)
            vs.append(v)
        ev.to_mesh_clear()
    return [round(min(us), 5), round(min(vs), 5), round(max(us), 5), round(max(vs), 5)]


def plate_metadata(name: str, shot: dict, cam, heading: float, width: int, height: int, bbox=None) -> dict:
    project, f_px = projector(cam, width, height)
    pos = cam.location
    # horizon: projection of a far point at camera height in the viewing direction
    look = (cam.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    far = pos + Vector((look.x, look.y, 0)).normalized() * 1e5
    _, horizon_v, _ = project(far)
    floor_pts = [(-3.0, -1.0), (3.0, -1.0), (3.0, -6.0), (-3.0, -6.0)]
    floor_img = [project(Vector((x, y, 0)))[:2] for x, y in floor_pts]
    wall_pts = [(-3.0, 0.5), (3.0, 0.5), (3.0, 3.5), (-3.0, 3.5)]
    wall_img = [project(Vector((x, 0.0, z)))[:2] for x, z in wall_pts]
    # proxy tyre contacts (car frame: x forward, y left)
    rot = Matrix.Rotation(math.radians(heading), 3, "Z")
    contacts = {}
    for label, (lx, ly) in {
        "front_left": (WHEELBASE / 2, TRACK / 2),
        "front_right": (WHEELBASE / 2, -TRACK / 2),
        "rear_left": (-WHEELBASE / 2, TRACK / 2),
        "rear_right": (-WHEELBASE / 2, -TRACK / 2),
    }.items():
        w = ANCHOR + rot @ Vector((lx, ly, 0.0))
        u, v, depth = project(w)
        contacts[label] = {"u": round(u, 5), "v": round(v, 5), "depthM": round(depth, 3), "floor": [round(w.x, 4), round(w.y, 4)]}
    # proxy silhouette bbox from its box corners (approximation of a typical car)
    xs, ys = [], []
    for lx in (-CAR_L / 2, CAR_L / 2):
        for ly in (-CAR_W / 2, CAR_W / 2):
            for lz in (0.0, CAR_H):
                u, v, _ = project(ANCHOR + rot @ Vector((lx, ly, lz)))
                xs.append(u)
                ys.append(v)
    anchor_u, anchor_v, anchor_depth = project(ANCHOR)
    px_per_m = f_px / anchor_depth / width  # normalised (fraction of width per metre) at the anchor
    look_down = math.degrees(math.asin(-look.z))
    return {
        "image": f"{name}.jpg",
        "shadow": f"{name}-shadow.png",
        "size": [width, height],
        "camera": {
            "position": [round(c, 4) for c in pos],
            "heightM": round(pos.z, 4),
            "distanceToAnchorM": round(shot["dist"], 4),
            "orbitDeg": shot["orbit"],
            "pitchDownDeg": round(look_down, 3),
            "hfovDeg": HFOV_DEG,
            "focalPx": round(f_px, 3),
            "focalNorm": round(f_px / width, 6),
        },
        "horizonV": round(horizon_v, 5),
        "floorHomography": homography(floor_pts, floor_img),
        "wallHomography": homography(wall_pts, wall_img),
        "anchor": {"floor": [ANCHOR.x, ANCHOR.y], "u": round(anchor_u, 5), "v": round(anchor_v, 5), "depthM": round(anchor_depth, 4), "widthPerMetre": round(px_per_m, 6)},
        "vehicle": {
            "view": shot["view"],
            "headingDeg": round(heading, 3),
            "targetWidthRatio": shot["target_width"],
            "proxy": {"lengthM": CAR_L, "widthM": CAR_W, "heightM": CAR_H, "wheelbaseM": WHEELBASE, "trackM": TRACK},
            "proxyBBox": bbox or [round(min(xs), 5), round(min(ys), 5), round(max(xs), 5), round(max(ys), 5)],
            "proxyContacts": contacts,
        },
        "wall": {"albedo": list(WALL_ALBEDO), "brandArea": {"xMin": -SLAT_INNER, "xMax": SLAT_INNER, "zMin": 0.0, "zMax": ROOM_H}},
    }


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
def setup_render(width: int, samples: int):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.01
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 8
    scene.cycles.glossy_bounces = 4
    scene.cycles.diffuse_bounces = 4
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.cycles.blur_glossy = 0.5
    scene.render.resolution_x = width
    scene.render.resolution_y = int(round(width * 3 / 4))
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new("dark")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0, 0, 0, 1)
    scene.view_settings.view_transform = "AgX"
    for look in ("AgX - Medium High Contrast", "AgX - Base Contrast", "None"):
        try:
            scene.view_settings.look = look
            break
        except TypeError:
            continue
    scene.view_settings.exposure = 0.0
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "16"
    scene.render.image_settings.color_mode = "RGB"


def setup_compositor():
    """Subtle photographic glow around the light sources (lens/sensor bloom)."""
    scene = bpy.context.scene
    scene.use_nodes = True
    tree = scene.node_tree
    tree.nodes.clear()
    rl = tree.nodes.new("CompositorNodeRLayers")
    glare = tree.nodes.new("CompositorNodeGlare")
    glare.glare_type = "FOG_GLOW"
    glare.quality = "HIGH"
    glare.mix = -0.82  # mostly the original image
    glare.threshold = 1.2
    glare.size = 7
    out = tree.nodes.new("CompositorNodeComposite")
    tree.links.new(rl.outputs["Image"], glare.inputs["Image"])
    tree.links.new(glare.outputs["Image"], out.inputs["Image"])


def set_floor_gloss(enabled: bool) -> None:
    """Switch the floor's lacquer coat and specular layer on (plate) or off (reflection pass)."""
    bsdf = bpy.data.materials["oak_floor"].node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Coat Weight"].default_value = FLOOR_COAT if enabled else 0.0
    bsdf.inputs["Specular IOR Level"].default_value = 0.5 if enabled else 0.0


def render_to(path: str):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def main() -> None:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    width = 800 if args.preview else args.width
    samples = 16 if args.preview else args.samples
    setup_render(width, samples)
    setup_compositor()
    build_room(args.assets)
    os.makedirs(args.out, exist_ok=True)
    height = int(round(width * 3 / 4))

    if args.reference_view:
        cam = make_camera(Vector((0, -8.6, 0.8)), Vector((0, 0, 0.8)))
        render_to(os.path.join(args.out, "reference-view.png"))
        return

    meta_path = os.path.join(args.out, "plates.json")
    meta = {"version": 1, "plates": {}}
    if os.path.isfile(meta_path):
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    meta["room"] = {
        "description": "AutoExperten Standard showroom (processor/showroom3d/render_plates.py)",
        "anchor": [ANCHOR.x, ANCHOR.y],
        "wallPlane": "y = 0 (x right, z up), floor z = 0, metres",
        "hfovDeg": HFOV_DEG,
    }
    passes = set(args.passes.split(","))
    if args.no_shadow:
        passes.discard("shadow")
    scene = bpy.context.scene
    for name in [s for s in args.shots.split(",") if s]:
        shot = SHOTS[name]
        position, target, heading = shot_camera(shot)
        cam = make_camera(position, target)
        bbox = solve_camera(cam, shot, heading, width, height)
        if "plate" in passes:
            scene.render.resolution_x, scene.render.resolution_y = width, height
            scene.cycles.samples = samples
            render_to(os.path.join(args.out, f"{name}.png"))
        if "shadow" in passes:
            sw = 400 if args.preview else args.shadow_width
            scene.render.resolution_x, scene.render.resolution_y = sw, int(round(sw * 3 / 4))
            scene.cycles.samples = 16 if args.preview else args.shadow_samples
            render_to(os.path.join(args.out, f"{name}-shadow-base.png"))
            root, parts = build_proxy(heading, visible=args.debug_proxy)
            render_to(os.path.join(args.out, f"{name}-shadow-proxy.png"))
            for p in parts:
                bpy.data.objects.remove(p, do_unlink=True)
            bpy.data.objects.remove(root, do_unlink=True)
        if "reflection" in passes:
            # same view, resolution and samples as the shadow-pass base render, gloss-free floor:
            # base − gloss-free = the floor's own reflection (postprocess_plates.py)
            sw = 400 if args.preview else args.shadow_width
            scene.render.resolution_x, scene.render.resolution_y = sw, int(round(sw * 3 / 4))
            scene.cycles.samples = 16 if args.preview else args.shadow_samples
            set_floor_gloss(False)
            render_to(os.path.join(args.out, f"{name}-glossfree.png"))
            set_floor_gloss(True)
        meta["plates"][name] = plate_metadata(name, shot, cam, heading, width, height, bbox)
        bpy.data.objects.remove(cam, do_unlink=True)
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
    if args.save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(args.out, "showroom.blend"))


if __name__ == "__main__":
    main()
