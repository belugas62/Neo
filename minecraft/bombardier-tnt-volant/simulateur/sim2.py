"""Simulateur v2 : cycle complet du moteur (2 modules) + duplicateur.

Modele (Java Edition, d'apres le code de PistonStructureResolver / moveBlocks /
PistonMovingBlockEntity) :
- ordre toPush exact (lignes, branches, reorder en cas de collision)
- boucle de deplacement en ordre inverse ; un corail dont le support devient
  bloc 36 casse et met a jour ses voisins (=> TNT alimentee amorcee = duplication)
- arrivee des blocs 36 dans l'ordre de creation ; rail/corail sans support cassent ;
  TNT alimentee a l'arrivee ou mise a jour par un voisin qui arrive = consommee
- rail detecteur (avec wagonnet) : alimentation forte du bloc dessous ; son arrivee
  ne met a jour que ses 6 voisins (=> TNT "BUD")
- trajectoire de la TNT amorcee (saut de 0.2, gravite 0.04, frottement 0.98,
  derive horizontale max 0.02/tick) et balayage par les blocs qui bougent ensuite.
"""
from sim import (B, Resolver, add, neg, DIRS, DOWN, UP, NORTH, SOUTH, WEST, EAST,
                 is_sticky, tnt_powered, sturdy)
import copy

NOCOLLIDE = {'rail', 'fan'}

def entity_box(t):
    """Boite (relative au coin du bloc d'origine) de la TNT amorcee t ticks apres."""
    y, v = 0.0, 0.2
    for _ in range(t):
        v -= 0.04; y += v; v *= 0.98
    d = 0.02 * t
    return (0.01 - d, 0.99 + d), (y, y + 0.98), (0.01 - d, 0.99 + d)

def overlap(a, b): return a[0] < b[1] and b[0] < a[1]

class Machine:
    def __init__(self, world, pistons):
        self.w = world
        self.pistons = pistons   # pistons et observateurs nommes -> position (suivie a chaque mouvement)
        self.t = 0
        self.entities = []       # (t_spawn, pos)
        self.log = []
        self.dupes = 0
        self.errors = []
        self.warn = []

    def err(self, m):
        self.errors.append(f't={self.t}: {m}'); self.log.append('  !! ' + m)

    def updates_at(self, positions, why):
        for m in positions:
            b = self.w.get(m)
            if b is not None and b.kind == 'tnt' and tnt_powered(self.w, m):
                self.err(f'TNT {m} amorcee hors poussee par {why} (consommee)')
                del self.w[m]

    def observer_updates(self, opos):
        o = self.w[opos]
        out = add(opos, neg(o.p['facing']))
        self.updates_at([out] + [add(out, d) for d in DIRS], f'observateur {opos}')

    def move(self, pname, extending, label):
        w = self.w
        ppos = self.pistons[pname]
        pb = w[ppos]
        pdir = pb.p['facing']
        if not extending:
            head = add(ppos, pdir)
            if w.get(head) is not None and w[head].kind == 'piston_head': del w[head]
            pb.p['extended'] = False
        r = Resolver(w, ppos, pdir, extending)
        if not r.resolve():
            self.err(f'{label}: structure non deplacable (limite 12 ou bloc fixe)'); return None
        tp, d = r.to_push, r.push_dir
        states = [w[p] for p in tp]
        self.log.append(f't={self.t} {label}: {len(tp)} blocs ' + ' '.join(f'{s.kind}{p}' for s, p in zip(states, tp)))
        vacated = set(tp)
        order = []
        dupes_here = []
        def nb_update(at):
            for dd in DIRS:
                m = add(at, dd); b = w.get(m)
                if b is None: continue
                if b.kind == 'tnt' and tnt_powered(w, m):
                    dupes_here.append(m); del w[m]; nb_update(m)
                elif b.kind == 'rail' and not sturdy(w.get(add(m, DOWN))):
                    self.err(f'rail {m} casse pendant la poussee'); del w[m]; nb_update(m)
        for k in range(len(tp)-1, -1, -1):
            p = tp[k]; dest = add(p, d)
            vacated.discard(dest)
            w[dest] = B('moving', state=states[k])
            order.append((dest, states[k], p))
            for dd in DIRS:
                m = add(dest, dd); b = w.get(m)
                if b is not None and b.kind == 'fan' and add(m, b.p['support']) == dest:
                    del w[m]; nb_update(m)
        for v in vacated:
            if v in w: del w[v]
        if extending:
            w[add(ppos, pdir)] = B('piston_head'); pb.p['extended'] = True
        for p in dupes_here:
            self.log.append(f'  >>> DUPLICATION : TNT amorcee en {p}')
            self.dupes += 1; self.entities.append((self.t, p))
        # balayage des entites par les blocs qui bougent (t .. t+2)
        for (t0, ep) in self.entities:
            for dt in (0, 1, 2):
                age = self.t + dt - t0
                if age < 0: continue
                ex, ey, ez = entity_box(age)
                ebox = ((ep[0]+ex[0], ep[0]+ex[1]), (ep[1]+ey[0], ep[1]+ey[1]), (ep[2]+ez[0], ep[2]+ez[1]))
                for dest, st, src in order:
                    if st.kind in NOCOLLIDE: continue
                    sw = ((dest[0], dest[0]+1), (dest[1], dest[1]+1), (dest[2], dest[2]+1))
                    if all(overlap(ebox[i], sw[i]) for i in range(3)):
                        sev = 'PROJETTE (slime)' if st.kind == 'slime' else 'pousse un peu'
                        self.warn.append(f't={self.t}: {st.kind} {src}->{dest} touche la TNT amorcee (age {age}, si derive) -> {sev}')
        # suivi des positions des pistons deplaces
        for name, pp in list(self.pistons.items()):
            if pp in tp:
                self.pistons[name] = add(pp, d)
        return order

    def arrive(self, order, label):
        w = self.w
        for dest, st, src in order:
            w[dest] = st
            if st.kind == 'fan' and not sturdy(w.get(add(dest, st.p['support']))):
                self.err(f'{label}: corail {dest} detruit a l arrivee'); del w[dest]; continue
            if st.kind == 'rail' and not sturdy(w.get(add(dest, DOWN))):
                self.err(f'{label}: rail {dest} casse a l arrivee'); del w[dest]; continue
            if st.kind == 'tnt' and tnt_powered(w, dest):
                self.err(f'{label}: TNT {dest} amorcee a l arrivee (consommee)'); del w[dest]; continue
            for dd in DIRS:
                m = add(dest, dd); b = w.get(m)
                if b is not None and b.kind == 'tnt' and tnt_powered(w, m):
                    self.err(f'{label}: TNT {m} amorcee par l arrivee de {st.kind} {dest} (consommee)'); del w[m]
        self.log.append(f'  arrivee {label}: ' + ' '.join(f'{s.kind}{d_}' for d_, s, _ in order))

    def cycle(self):
        """Un cycle : OA s'allume -> SPA pousse B -> OB s'allume -> SPB tire A."""
        OA = self.pistons['OA']
        w = self.w
        t0 = self.t
        w[OA].p['powered'] = True; self.observer_updates(OA)
        o = self.move('SPA', True, 'SP_A pousse B')
        if o is None: return
        self.t = t0 + 2
        w[OA].p['powered'] = False; self.observer_updates(OA)
        # SP_A se retracte en "crachant" (bloc 36 finalise immediatement)
        head = add(self.pistons['SPA'], w[self.pistons['SPA']].p['facing'])
        self.arrive(o, 'B')
        if w.get(head) is not None and w[head].kind == 'piston_head': del w[head]
        w[self.pistons['SPA']].p['extended'] = False
        self.updates_at([add(head, d) for d in DIRS] + [add(self.pistons['SPA'], d) for d in DIRS], 'retrait SP_A')
        OB2 = self.pistons['OB']
        self.t = t0 + 4
        w[OB2].p['powered'] = True; self.observer_updates(OB2)
        o2 = self.move('SPB', True, 'SP_B sort dans le vide')
        if o2 is None: return
        if o2: self.err('SP_B a pousse quelque chose en sortant')
        self.t = t0 + 6
        w[OB2].p['powered'] = False; self.observer_updates(OB2)
        head = add(self.pistons['SPB'], w[self.pistons['SPB']].p['facing'])
        self.updates_at([add(head, d) for d in DIRS], 'tete SP_B finalisee')
        o3 = self.move('SPB', False, 'SP_B tire A')
        if o3 is None: return
        self.t = t0 + 8
        self.arrive(o3, 'A')
        self.updates_at([add(self.pistons['SPB'], d) for d in DIRS], 'base SP_B finalisee')
        self.t = t0 + 10
