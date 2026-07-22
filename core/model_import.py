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


def obj_import(filepath: str):
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
        )
    elif ext == ".obj":
        print("      -> Calling wm.obj_import...")
        bpy.ops.wm.obj_import('EXEC_DEFAULT', filepath=str(path))  # Blender 4.2: obj_import runs cleanly in --background, no master-collection dance needed
    else:
        raise ValueError(f"Unsupported file extension: {ext}")

    print("      -> Import operator finished. Identifying meshes...")
    imported_objects = set(bpy.context.scene.objects) - objects_before

    # first identify meshes, but do not delete parents yet
    meshes = []
    for obj in imported_objects:
        if obj.type == "MESH":
            make_transparent(obj)
            meshes.append(obj)

    if not meshes:
        print(f"Warning: imported file '{filepath}' did not contain mesh objects.", file=sys.stderr)
        return

    print("      -> Clearing parents and applying transforms...")
    # clear parents and apply transforms
    with bpy.context.temp_override(selected_objects=meshes, selected_editable_objects=meshes):
        bpy.ops.object.parent_clear('EXEC_DEFAULT', type="CLEAR_KEEP_TRANSFORM")
        bpy.ops.object.transform_apply('EXEC_DEFAULT', location=True, rotation=True, scale=True)

    print("      -> Deleting non-mesh parents...")
    # delete imported non-mesh parents now that transforms are baked
    for obj in list(imported_objects):
        if obj.type != "MESH":
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except ReferenceError:
                pass

    print("      -> Joining meshes...")
    # keep only meshes that actually have geometry
    meshes = [m for m in meshes if m.data and len(m.data.vertices) > 0]

    if not meshes:
        print(f"Warning: imported file '{filepath}' did not contain usable mesh data.", file=sys.stderr)
        return

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
    
    print(f"      -> Successfully finished importing {filepath}!")

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
