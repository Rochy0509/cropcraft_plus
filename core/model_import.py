# Copyright 2024 INRAE, French National Research Institute for Agriculture, Food and Environment
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import bpy
import sys
from pathlib import Path


def obj_import(filepath: str, keep_empties: bool = False):
    """
    Import a USD or OBJ file, join meshes into a single named object.

    Args:
        filepath: path to the asset file (.usd, .usda, .usdc, .usdz, .obj).
        keep_empties: if True, preserve non-mesh objects (e.g. snap-point empties
                      from tomato_fruited.usda) and return them alongside the mesh.
                      If False (default), non-mesh objects are deleted as before.
    Returns:
        (mesh_object, list_of_empties) if keep_empties is True,
        None if keep_empties is False or no meshes were found.
    """
    print(f"      -> Inside obj_import for: {filepath}")
    objects_before = set(bpy.context.scene.objects)
    path = Path(filepath)
    ext = path.suffix.lower()

    if ext in (".usd", ".usda", ".usdc", ".usdz"):
        print("      -> Calling usd_import...")
        bpy.ops.wm.usd_import(
            'EXEC_DEFAULT',
            filepath=str(path),
            import_meshes=True,
            import_materials=True,
            import_usd_preview=True,
            read_mesh_uvs=True,
            read_mesh_colors=True,
            import_subdiv=True,  # Blender 4.2: usd_import uses 'import_subdiv' (not 'import_subdivision')
            import_lights=False,  # Blender 4.2: prevent embedded DomeLight prims from polluting the scene lighting
        )
    elif ext == ".obj":
        print("      -> Calling wm.obj_import...")
        bpy.ops.wm.obj_import('EXEC_DEFAULT', filepath=str(path))  # Blender 4.2: obj_import runs cleanly in --background, no master-collection dance needed
    else:
        raise ValueError(f"Unsupported file extension: {ext}")

    print("      -> Import operator finished. Identifying meshes...")
    imported_objects = set(bpy.context.scene.objects) - objects_before

    # Blender 4.2: strict filter — separate meshes, socket empties, and USD garbage
    # USD imports bring in root Xforms, _materials empties, env_light, etc. that must not
    # end up in the plant collection (Geometry Nodes would scatter them instead of the mesh).
    meshes = []
    sockets = []
    garbage = []

    for obj in imported_objects:
        if obj.type == "MESH":
            make_transparent(obj)
            meshes.append(obj)
        elif obj.type == "EMPTY" and "socket" in obj.name.lower():
            sockets.append(obj)
        else:
            # Catch env_light, _materials, and root USD xforms
            garbage.append(obj)

    # 1. Nuke the USD garbage immediately
    for obj in garbage:
        bpy.data.objects.remove(obj, do_unlink=True)

    if not meshes:
        print(f"Warning: imported file '{filepath}' did not contain mesh objects.", file=sys.stderr)
        if not keep_empties:
            for obj in sockets:
                bpy.data.objects.remove(obj, do_unlink=True)
            sockets = []
        return (None, sockets) if keep_empties else None

    print("      -> Clearing parents and applying transforms...")
    # clear parents and apply transforms on meshes only (empties keep their USD hierarchy)
    with bpy.context.temp_override(selected_objects=meshes, selected_editable_objects=meshes):
        bpy.ops.object.parent_clear('EXEC_DEFAULT', type="CLEAR_KEEP_TRANSFORM")
        bpy.ops.object.transform_apply('EXEC_DEFAULT', location=True, rotation=True, scale=True)

    # 2. Handle the sockets safely
    if not keep_empties:
        print("      -> Deleting non-mesh parents...")
        for obj in sockets:
            bpy.data.objects.remove(obj, do_unlink=True)
        sockets = []
    else:
        # Blender 4.2: keep socket empties but unlink them from the plant collection so
        # Geometry Nodes ignores them.  Stash them in 'resources' so they survive without
        # polluting the instancing collection.
        print(f"      -> Preserved {len(sockets)} socket empty object(s) as snap points")
        for emp in sockets:
            for coll in emp.users_collection:
                coll.objects.unlink(emp)
            if "resources" in bpy.data.collections:
                bpy.data.collections["resources"].objects.link(emp)

    print("      -> Joining meshes...")
    # keep only meshes that actually have geometry
    meshes = [m for m in meshes if m.data and len(m.data.vertices) > 0]

    if not meshes:
        print(f"Warning: imported file '{filepath}' did not contain usable mesh data.", file=sys.stderr)
        return (None, sockets) if keep_empties else None

    active = meshes[0]
    bpy.context.view_layer.objects.active = active

    # build a object name from the filename
    base_name = path.stem  # file name without extension
    merged_name = f"{base_name}"

    if len(meshes) == 1:
        active.name = merged_name
    else:
        # make the operator see exactly these selections
        with bpy.context.temp_override(
            object=active,
            active_object=active,
            selected_objects=meshes,
            selected_editable_objects=meshes,
        ):
            bpy.ops.object.join('EXEC_DEFAULT')
        bpy.context.view_layer.objects.active.name = merged_name

    mesh_obj = bpy.context.view_layer.objects.active

    if keep_empties and sockets:
        # Blender 4.2: reparent socket empties to the joined mesh so matrix_local
        # gives the snap-point offset in the mesh's local space.
        for emp in sockets:
            emp.parent = mesh_obj

    print(f"      -> Successfully finished importing {filepath}!")

    if keep_empties:
        return (mesh_obj, sockets)
    return None

def make_transparent(obj: bpy.types.Object):
    """
    This function modifies the given Blender object to make its material
    transparent by linking the alpha output of its image texture node
    to the alpha input of its Principled BSDF shader node.

    Parameters:
    obj (bpy.types.Object): The Blender object to be modified.
                            It should be of type 'MESH' and have a
                            material with a node tree containing both
                            a Principled BSDF node and an image texture node.
    """
    material = obj.active_material
    if material is None or material.node_tree is None:
        return

    nodes = material.node_tree.nodes

    bsdf_node = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
    image_node = next((node for node in nodes if node.type == "TEX_IMAGE"), None)

    if bsdf_node and image_node:
        # create a link from the image node's alpha output to the BSDF's alpha input
        links = material.node_tree.links

        links.new(image_node.outputs["Alpha"], bsdf_node.inputs["Alpha"])
