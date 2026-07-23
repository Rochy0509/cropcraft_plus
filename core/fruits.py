# Copyright 2024 INRAE, French National Research Institute for Agriculture, Food and Environment
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import bpy
import math
import mathutils
import random
import os

from . import config
from .beds import Beds, _apply_emission_material
from .model_import import obj_import
from .plant_manager import PlantManager


class FruitScatterer:
    """
    Places fruit mesh instances at snap-point empties on realized plant instances.
    Uses the post-scatter Python approach (Option A - Python drives Geonodes):
      - After beds.create_beds(), each PlantState in field.state has (x,y,z,roll,pitch,yaw,height,filename)
      - The 'index' attribute on the bed mesh tells GN which model to instance per point
      - Python and GN share the same index list, so they are always in sync
      - For plants whose model has snap points (stored in beds.snap_points[filename]),
        compute each snap point's world transform = plant_instance_matrix * snap_point_local_matrix
      - Copy the fruit mesh to each snap point, filtered by density, scaled by plant scale * fruit scale
    """

    def __init__(self, field: config.Field, beds: Beds):
        self.field = field
        self.beds = beds
        self.rand = random.Random(random.getrandbits(32))
        self.fruit_meshes = {}   # Blender 4.2: maps fruit_type -> imported fruit mesh object (template for copying)
        self.fruit_count = 0

    def load_fruits(self):
        """
        Import fruit model files into the 'fruits' collection.
        Each unique fruit_type is imported once; the mesh is used as a template for all copies.
        """
        fruits_collection = bpy.data.collections["fruits"]

        view_layer = bpy.context.view_layer
        scene_layer_coll = view_layer.layer_collection
        fruits_layer_coll = scene_layer_coll.children["fruits"]  # Blender 4.2: fruits is at scene root, not under resources

        for fruit_spec in self.field.fruits:
            fruit_type = fruit_spec.fruit_type
            if fruit_type not in self.beds.plant_mgr.plant_groups:
                raise RuntimeError(
                    f"Error: fruit type '{fruit_type}' is unknown. "
                    f"Ensure a description.yaml exists in assets/plants/{fruit_type}/."
                )

            models = self.beds.plant_mgr.plant_groups[fruit_type]

            # Blender 4.2: import each fruit model once with keep_empties=False (fruits have no snap points)
            for model in models:
                view_layer.active_layer_collection = fruits_layer_coll
                obj_import(model.filepath, keep_empties=False)
                # find the imported mesh by scanning the fruits collection
                mesh_obj = None
                for obj in reversed(list(fruits_collection.objects)):
                    if obj.type == "MESH":
                        mesh_obj = obj
                        break

                if mesh_obj is not None:
                    # Blender 4.2: store the fruit template mesh keyed by fruit_type for later duplication
                    self.fruit_meshes[fruit_type] = mesh_obj
                    # hide the template so it doesn't appear in the scene — only copies will be visible
                    mesh_obj.hide_set(True)
                    mesh_obj.hide_render = True
                    print(f"      -> Loaded fruit template: {fruit_type} -> {mesh_obj.name}")

    def scatter_fruits(self):
        """
        For each plant instance in field.state that uses a model with snap points,
        place fruit copies at the snap point positions, scaled and rotated appropriately.
        """
        if not self.field.fruits:
            print(">>> No fruit specs configured, skipping fruit scattering")
            return

        if not self.beds.snap_points:
            print(">>> No snap points found on any plant models, skipping fruit scattering")
            return

        fruits_collection = bpy.data.collections["fruits"]

        # Create a subcollection per fruit spec
        view_layer = bpy.context.view_layer
        scene_layer_coll = view_layer.layer_collection
        fruits_layer_coll = scene_layer_coll.children["fruits"]  # Blender 4.2: fruits is at scene root, not under resources

        for fruit_spec in self.field.fruits:
            fruit_subcoll = bpy.data.collections.new(fruit_spec.name)
            fruits_collection.children.link(fruit_subcoll)
            fruit_layer_coll = fruits_layer_coll.children[fruit_spec.name]

            fruit_mesh = self.fruit_meshes.get(fruit_spec.fruit_type)
            if fruit_mesh is None:
                print(f"Warning: no fruit mesh loaded for type '{fruit_spec.fruit_type}', skipping")
                continue

            plant_count = 0
            snap_count = 0
            placed_count = 0

            # Iterate the field state to find plants whose model has snap points
            bed_index = 0
            plant_index = 0
            for bed, bed_state in zip(self.field.beds, self.field.state.beds):
                # Get the plant models for this bed to compute scale
                bed_plant_type, bed_models = self.beds.bed_plant_groups[bed.name]
                nb_plants = len(bed_models)

                for row_state in bed_state.rows:
                    for crop in row_state.crops:
                        # Blender 4.2: only populate fruits on plants whose model file has snap points
                        if crop.filename not in self.beds.snap_points:
                            plant_index += 1
                            continue

                        plant_count += 1

                        # Compute the plant scale: crop.height / original_model_height
                        model_heights = {m.filename: m.height for m in bed_models}
                        model_height = model_heights.get(crop.filename, 1.0)
                        if model_height > 0:
                            plant_scale = crop.height / model_height
                        else:
                            plant_scale = 1.0

                        # Compute plant instance world matrix from PlantState (x,y,z, roll,pitch,yaw)
                        plant_loc = mathutils.Vector((crop.x, crop.y, crop.z))
                        # Blender 4.2: rotation order in PlantState is (roll, pitch, yaw) matching XYZ euler
                        plant_rot = mathutils.Euler((crop.roll, crop.pitch, crop.yaw), 'XYZ')
                        plant_matrix = mathutils.Matrix.LocRotScale(
                            plant_loc, plant_rot,
                            mathutils.Vector((1.0, 1.0, 1.0))
                        )
                        # Apply plant scale as a separate scale matrix
                        plant_scale_matrix = mathutils.Matrix.Scale(plant_scale, 4)

                        # Get snap points for this model
                        snap_empties = self.beds.snap_points[crop.filename]

                        for snap_empty in snap_empties:
                            snap_count += 1

                            # Blender 4.2: density filter — skip snap points probabilistically
                            if self.rand.random() > fruit_spec.density:
                                continue

                            # Compute snap point local matrix (no plant transform yet)
                            snap_local_matrix = snap_empty.matrix_local.copy()

                            # Create a copy of the fruit mesh
                            fruit_copy = self._copy_fruit(fruit_mesh, fruit_spec, bed.name, plant_index, snap_empty.name)
                            if fruit_copy is None:
                                continue

                            # Scale the fruit copy by the fruit spec's scale factor
                            fruit_scale_matrix = mathutils.Matrix.Scale(fruit_spec.scale, 4)

                            # Apply combined transform: plant_matrix * plant_scale * snap_point_local * fruit_scale
                            fruit_copy.matrix_world = plant_matrix @ plant_scale_matrix @ snap_local_matrix @ fruit_scale_matrix

                            # Link to the fruit subcollection
                            fruit_layer_coll.collection.objects.link(fruit_copy)
                            placed_count += 1

                        plant_index += 1

            print(f"    -> Fruit '{fruit_spec.name}': {plant_count} plants, {snap_count} snap points, {placed_count} fruits placed")
            self.fruit_count += placed_count

        print(f">>> Total fruits placed: {self.fruit_count}")

    def _copy_fruit(self, template_mesh, fruit_spec, bed_name, plant_index, snap_name):
        """
        Create a shallow copy of the template fruit mesh object.
        Shares the mesh data (linked duplicate) for efficiency — only transform differs.
        """
        # Blender 4.2: linked duplicate shares mesh data to avoid duplicating ~500 vertices per fruit × 28 per plant
        name = f"fruit_{bed_name}_{plant_index}_{snap_name}"
        fruit_copy = bpy.data.objects.new(name, template_mesh.data)
        return fruit_copy

    def apply_label_materials(self, label_colors: config.LabelColors):
        """
        Replace fruit materials with an emission material using the fruit label color
        for semantic segmentation rendering.
        """
        color = tuple(c / 255.0 for c in label_colors.fruit) + (1.0,)
        fruits_collection = bpy.data.collections["fruits"]
        for subcoll in fruits_collection.children.values():
            _apply_emission_material(list(subcoll.objects), color)