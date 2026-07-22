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

import sys
import os
import random

this_module_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, this_module_dir)

import core


def configure_random_seed(field: core.config.Field):
    if field.seed is None:
        seed = random.getrandbits(32)
        field.seed = seed

    random.seed(field.seed)


def main(argv: list):
    
    # Whatever comes next (maybe lighting, rendering, or exporting)
    args = argv[argv.index('--') + 1:]
    config_file = args[0]
    output_dir = args[1]

    try:
        cfg = core.parser.load_yaml_config(config_file)
    except core.parser.ParserError as e:
        print(f"Error: Failed to load config file '{config_file}': {e}", file=sys.stderr)
        exit(1)

    field = cfg.field

    configure_random_seed(field)
    env_path = cfg.render.env_path if cfg.render is not None else None
    env_rotation_deg = cfg.render.env_rotation_deg if cfg.render is not None else 0.0
    core.base.create_blender_context(env_path, env_rotation_deg)

    try:
        beds = core.beds.Beds(field)
        beds.load_plants()
        print(">>> Starting to create beds...")
        beds.create_beds()
        print(">>> Finished creating beds!")

    # Whatever comes next (maybe lighting, rendering, or exporting)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        exit(2)

    print(">>> Initializing Ground...")

    print("  -> Calling Ground(field, beds)...")
    ground = core.ground.Ground(field, beds)
    
    print("  -> Calling load_weeds()...")
    ground.load_weeds(beds.plant_mgr)
    
    print("  -> Calling load_stones()...")
    ground.load_stones()
    
    print(">>> Creating Ground Plane...")
    ground.create_plane()
    
    print(">>> Creating Weeds...")
    ground.create_weeds()
    
    print(">>> Creating Stones...")
    ground.create_stones()
    print(">>> Finished Ground Operations!")

    look_at = beds.get_center_pos()
    look_at.x = 5.
    print(">>> Creating Camera...")
    core.base.create_camera(look_at)

    print(">>> Starting Exports...")
    for output in cfg.outputs:
        print(f">>> Exporting item: {output}")
        output.export(output_dir, field)
    print(">>> Finished Exports!")

    if cfg.render is not None:
        print(">>> Setting up Camera Animation...")
        core.base.setup_camera_animation(cfg.render, beds.get_end_pos())
        
        print(">>> Rendering Unlabeled Animation...")
        core.base.render_animation(cfg.render, labeled=False)
        
        print(">>> Applying Label Materials...")
        beds.apply_label_materials(cfg.render.label_colors)
        ground.apply_label_materials(cfg.render.label_colors)
        
        print(">>> Rendering Labeled Animation...")
        core.base.render_animation(cfg.render, labeled=True)
        print(">>> Finished Rendering!")

    print(f'Generated seed: {field.seed}')


if __name__ == '__main__':
    main(sys.argv)
