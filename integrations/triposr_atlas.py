"""Run the pinned TripoSR xatlas/OpenGL bake stage in Studio's Python."""

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("packages", type=Path)
    parser.add_argument("--mesh", type=Path)
    parser.add_argument("--resolution", type=int)
    args = parser.parse_args()
    sys.path[:0] = [str(args.source), str(args.packages)]
    import numpy as np
    from tsr.bake_texture import make_atlas, rasterize_position_atlas

    if args.mesh:
        with np.load(args.mesh / "surface.npz") as surface:
            mesh = SimpleNamespace(vertices=surface["vertices"], faces=surface["faces"])
    else:
        mesh = SimpleNamespace(vertices=np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]),
                               faces=np.array([[0, 1, 2]], dtype=np.int32))
    resolution = args.resolution or 256
    padding = round(max(2, resolution / 256))
    atlas = make_atlas(mesh, resolution, padding)
    positions = rasterize_position_atlas(mesh, atlas["vmapping"], atlas["indices"], atlas["uvs"],
                                         resolution, padding)
    if not np.any(positions[..., 3]):
        raise ValueError("TripoSR generated an empty UV position atlas.")
    if args.mesh:
        np.savez(args.mesh / "atlas.npz", **atlas, positions=positions)


if __name__ == "__main__":
    main()
