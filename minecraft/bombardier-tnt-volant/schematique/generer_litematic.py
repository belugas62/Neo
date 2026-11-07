"""Genere bombardier_tnt_volant.litematic a partir de la conception validee
(../simulateur/design.py), pour Litematica (Minecraft Java).

Usage :
    pip install litemapy
    python3 generer_litematic.py

La schematique est en configuration "ECART" (module avant deja avance d'un bloc),
c'est-a-dire la configuration de construction. Sans rotation, la machine vole vers +X (est).
Le bouton de demarrage n'est volontairement PAS inclus.
"""
import os
import sys

from litemapy import BlockState, Entity, Region
from nbtlib.tag import Compound, Double, Float, List, String

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'simulateur'))
from design import build                      # noqa: E402
from sim import EAST                          # noqa: E402

# 1.21.4 : Litematica met a jour les blocs avec le DataFixer de ta version (26.2).
MC_DATA_VERSION = 4189

# design (x de -1 a 4, y de -3 a 1, z de -1 a 2)  ->  region (0..5, 0..4, 0..3)
DX, DY, DZ = 1, 3, 1
SIZE = (6, 5, 4)


def blockstate(b):
    k = b.kind
    if k == 'slime':
        return BlockState('minecraft:slime_block')
    if k == 'glass':
        return BlockState('minecraft:glass')
    if k == 'stone':
        return BlockState('minecraft:stone')
    if k == 'tnt':
        return BlockState('minecraft:tnt', unstable='false')
    if k == 'rail':
        return BlockState('minecraft:detector_rail', powered='false', shape='east_west', waterlogged='false')
    if k == 'fan':
        return BlockState('minecraft:dead_tube_coral_wall_fan', facing='west', waterlogged='false')
    side = 'east' if b.p['facing'] == EAST else 'west'
    if k == 'sticky_piston':
        return BlockState('minecraft:sticky_piston', extended='false', facing=side)
    if k == 'observer':
        return BlockState('minecraft:observer', facing=side, powered='false')
    raise ValueError(k)


def main():
    world, _ = build()
    reg = Region(0, 0, 0, *SIZE)
    for (x, y, z), b in world.items():
        reg[x + DX, y + DY, z + DZ] = blockstate(b)

    # wagonnet pose sur le rail detecteur (design (1,-2,0) -> region (2,1,1))
    rail = next(p for p, b in world.items() if b.kind == 'rail')
    rx, ry, rz = rail[0] + DX, rail[1] + DY, rail[2] + DZ
    cart = Entity(Compound({
        'id': String('minecraft:minecart'),
        'Pos': List[Double]([Double(rx + 0.5), Double(ry), Double(rz + 0.5)]),
        'Motion': List[Double]([Double(0.0), Double(0.0), Double(0.0)]),
        'Rotation': List[Float]([Float(0.0), Float(0.0)]),
    }))
    reg.entities.append(cart)

    schem = reg.as_schematic(
        name='Bombardier TNT volant',
        author='Claude',
        description=("Bombardier volant a duplication de TNT (Java 26.2). Configuration ECART : "
                     "vole vers l'est. Demarrage : bouton sur la face visage de l'observateur avant."),
    )
    schem.mc_version = MC_DATA_VERSION
    out = os.path.join(HERE, 'bombardier_tnt_volant.litematic')
    schem.save(out)
    print('ecrit :', out, f'({sum(1 for _ in world)} blocs + 1 wagonnet)')


if __name__ == '__main__':
    main()
