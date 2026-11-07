"""Conception : bombardier volant a duplication de TNT (Java).

Axe : +X = Est = sens de vol. +Z = Sud. +Y = haut.
Coordonnees donnees en configuration "ECART" (module avant deja avance d'un bloc),
qui est la configuration de construction (necessaire pour poser le wagonnet
sans amorcer la TNT).
"""
import sys
from sim import B, add, DIRS, DOWN, UP, NORTH, SOUTH, WEST, EAST, tnt_powered, strong_into
from sim2 import Machine

def build():
    w = {}
    P = {}
    # ---- Module ARRIERE (A) ----
    w[(-1,0,0)] = B('observer', facing=WEST, powered=False); P['OA'] = (-1,0,0)
    w[(0,0,0)]  = B('slime')                                  # S_A
    w[(0,1,0)]  = B('sticky_piston', facing=EAST, extended=False); P['SPA'] = (0,1,0)
    w[(0,-1,0)] = B('slime')                                  # D
    w[(0,-2,0)] = B('glass')                                  # pousseur du wagonnet
    w[(1,-2,0)] = B('rail', powered=True)                     # rail detecteur + wagonnet
    w[(1,-3,0)] = B('slime')                                  # P1 (sous le rail)
    w[(0,-1,-1)] = B('slime')
    w[(1,-1,-1)] = B('slime')
    w[(1,-2,-1)] = B('slime')
    w[(1,-3,-1)] = B('slime')
    # ---- Module AVANT (B), deja avance d'un bloc (ecart) ----
    w[(2,0,0)]  = B('sticky_piston', facing=WEST, extended=False); P['SPB'] = (2,0,0)
    w[(2,1,0)]  = B('slime')                                  # S_B
    w[(3,1,0)]  = B('observer', facing=EAST, powered=False); P['OB'] = (3,1,0)
    w[(2,1,1)]  = B('slime')
    w[(2,1,2)]  = B('slime')
    w[(2,0,2)]  = B('slime')
    w[(2,-1,2)] = B('slime')
    w[(2,-2,2)] = B('slime')
    w[(2,-3,2)] = B('slime')
    w[(2,-3,1)] = B('tnt')
    w[(3,-3,1)] = B('fan', support=EAST)                      # eventail de corail mort, mural, tourne vers l'ouest
    w[(4,-3,1)] = B('stone')                                  # support du corail
    return w, P

PISTONS = ('sticky_piston', 'piston')

def leak_check(m, when):
    """Aucun piston ne doit etre alimente par le rail/P1 (direct ou quasi-connectivite)."""
    w = m.w
    for pos, b in list(w.items()):
        if b.kind not in PISTONS: continue
        facing = b.p['facing']
        def emits_into(src, dst):
            s = w.get(src)
            if s is None: return False
            if s.kind == 'rail' and s.p.get('powered'): return True
            up = w.get(add(src, UP))
            if s.kind in ('slime', 'stone') and up is not None and up.kind == 'rail' and up.p.get('powered'): return True
            return False
        for d in DIRS:
            if d == facing: continue
            n = add(pos, d)
            if emits_into(n, pos):
                m.err(f'{when}: piston {pos} alimente par {w[n].kind} {n}')
        above = add(pos, UP)
        for d in DIRS:
            n = add(above, d)
            if n != pos and emits_into(n, above):
                m.err(f'{when}: piston {pos} alimente par quasi-connectivite via {n}')

def run(cycles=4, verbose=True):
    w, P = build()
    m = Machine(w, P)
    # demarrage : on pose un bouton sur la face de l'observateur avant -> OB s'allume
    t0 = m.t
    OB = m.pistons['OB']
    w[OB].p['powered'] = True; m.observer_updates(OB)
    o2 = m.move('SPB', True, 'SP_B sort dans le vide (demarrage)')
    m.t = t0 + 2
    w[OB].p['powered'] = False; m.observer_updates(OB)
    o3 = m.move('SPB', False, 'SP_B tire A (demarrage)')
    m.t = t0 + 4
    m.arrive(o3, 'A')
    leak_check(m, 'apres demarrage')
    # TNT alimentee ("BUD") ?
    for pos, b in w.items():
        if b.kind == 'tnt':
            m.log.append(f'  TNT {pos} alimentee (BUD) : {tnt_powered(w, pos)}')
    m.t = t0 + 6
    for i in range(cycles):
        m.cycle()
        leak_check(m, f'cycle {i+1}')
    if verbose:
        print('\n'.join(m.log))
    print(f'\nDUPLICATIONS : {m.dupes} en {cycles} cycles')
    print('ERREURS :', *m.errors, sep='\n  ') if m.errors else print('ERREURS : aucune')
    print('AVERTISSEMENTS :', *sorted(set(m.warn)), sep='\n  ') if m.warn else print('AVERTISSEMENTS : aucun')
    return m

if __name__ == '__main__':
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
