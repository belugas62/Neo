"""Mini-simulateur de l'ordre de deplacement des pistons (Java Edition).

Reproduit PistonStructureResolver (ordre de la liste toPush) et la boucle
de moveBlocks, pour verifier dans quel ordre les blocs deviennent des
"blocs 36", quand le corail casse, si la TNT est alimentee a ce moment-la,
et l'ordre d'arrivee des blocs.
"""
import sys

DOWN, UP, NORTH, SOUTH, WEST, EAST = (0,-1,0), (0,1,0), (0,0,-1), (0,0,1), (-1,0,0), (1,0,0)
DIRS = [DOWN, UP, NORTH, SOUTH, WEST, EAST]   # ordre de Direction.values()
NAMES = {DOWN:'down', UP:'up', NORTH:'north', SOUTH:'south', WEST:'west', EAST:'east'}

def add(a, b, k=1): return (a[0]+b[0]*k, a[1]+b[1]*k, a[2]+b[2]*k)
def neg(a): return (-a[0], -a[1], -a[2])
def axis(d): return 0 if d[0] else (1 if d[1] else 2)

class B:
    def __init__(self, kind, **p): self.kind, self.p = kind, p
    def __repr__(self): return self.kind + (str(self.p) if self.p else '')

STICKY = {'slime', 'honey'}
CONDUCTOR = {'slime', 'stone', 'tnt'}        # blocs pleins conducteurs utilises ici

def is_sticky(b): return b is not None and b.kind in STICKY
def can_stick(a, b):
    if a is None or b is None: return False
    if {a.kind, b.kind} == {'slime', 'honey'}: return False
    return is_sticky(a) or is_sticky(b)
def pushable(b):
    if b is None: return False
    if b.kind in ('moving', 'piston_head', 'obsidian'): return False
    if b.kind in ('piston', 'sticky_piston') and b.p.get('extended'): return False
    return True

class Resolver:
    def __init__(self, w, piston_pos, piston_dir, extending):
        self.w, self.piston_pos, self.extending = w, piston_pos, extending
        self.push_dir = piston_dir if extending else neg(piston_dir)
        self.start = add(piston_pos, piston_dir) if extending else add(piston_pos, piston_dir, 2)
        self.to_push = []
    def resolve(self):
        b = self.w.get(self.start)
        if b is None: return True            # sortie dans le vide
        if not pushable(b): return False
        if not self.line(self.start): return False
        i = 0
        while i < len(self.to_push):
            p = self.to_push[i]
            if is_sticky(self.w.get(p)) and not self.branch(p): return False
            i += 1
        return True
    def line(self, origin):
        w = self.w
        b = w.get(origin)
        if b is None or not pushable(b) or origin == self.piston_pos or origin in self.to_push:
            return True
        i = 1
        if i + len(self.to_push) > 12: return False
        while is_sticky(b):
            bp = add(origin, neg(self.push_dir), i)
            prev, b = b, w.get(bp)
            if b is None or not can_stick(prev, b) or not pushable(b) or bp == self.piston_pos: break
            i += 1
            if i + len(self.to_push) > 12: return False
        l = 0
        for j in range(i-1, -1, -1):
            self.to_push.append(add(origin, neg(self.push_dir), j)); l += 1
        i1 = 1
        while True:
            bp = add(origin, self.push_dir, i1)
            if bp in self.to_push:
                k = self.to_push.index(bp)
                self.reorder(l, k)
                for j1 in range(0, k + l + 1):
                    p2 = self.to_push[j1]
                    if is_sticky(w.get(p2)) and not self.branch(p2): return False
                return True
            b = w.get(bp)
            if b is None: return True
            if not pushable(b) or bp == self.piston_pos: return False
            if len(self.to_push) >= 12: return False
            self.to_push.append(bp); l += 1; i1 += 1
    def reorder(self, offsets, index):
        tp = self.to_push
        a, b_, c = tp[:index], tp[len(tp)-offsets:], tp[index:len(tp)-offsets]
        self.to_push = a + b_ + c
    def branch(self, frm):
        b = self.w.get(frm)
        for d in DIRS:
            if axis(d) != axis(self.push_dir):
                bp = add(frm, d)
                if can_stick(self.w.get(bp), b) and not self.line(bp): return False
        return True

def strong_into(w, pos):
    """Puissance forte recue par le bloc conducteur en pos."""
    up = w.get(add(pos, UP))
    if up is not None and up.kind == 'rail' and up.p.get('powered'): return True
    for d in DIRS:
        o = w.get(add(pos, d))
        if o is not None and o.kind == 'observer' and o.p.get('powered') and add(add(pos, d), neg(o.p['facing'])) == pos:
            return True
    return False

def tnt_powered(w, pos):
    for d in DIRS:
        n = add(pos, d); b = w.get(n)
        if b is None: continue
        if b.kind == 'rail' and b.p.get('powered'): return True
        if b.kind == 'observer' and b.p.get('powered') and add(n, neg(b.p['facing'])) == pos: return True
        if b.kind in CONDUCTOR and strong_into(w, n): return True
    return False

def sturdy(b): return b is not None and b.kind in ('slime', 'stone', 'tnt', 'honey', 'observer', 'sticky_piston', 'piston', 'glass')

def simulate_move(w, piston_pos, piston_dir, extending, label, verbose=True):
    log = []
    r = Resolver(w, piston_pos, piston_dir, extending)
    if not r.resolve():
        print(f'[{label}] ECHEC: structure non deplacable ({len(r.to_push)} blocs)'); return None
    tp = r.to_push
    d = r.push_dir
    if verbose:
        print(f'[{label}] {len(tp)} blocs, liste toPush:')
        for i, p in enumerate(tp): print(f'   {i:2d} {p} {w[p]}')
    states = [w[p] for p in tp]
    vacated = set(tp)
    dupes, problems = [], []
    if not extending:
        head = add(piston_pos, piston_dir)
        if head in w and w[head].kind == 'piston_head': del w[head]
    order = []
    def neighbor_update(at):
        for dd in DIRS:
            m = add(at, dd); b = w.get(m)
            if b is None: continue
            if b.kind == 'tnt' and tnt_powered(w, m):
                dupes.append(m); log.append(f'  >>> TNT en {m} amorcee pendant la poussee (DUPLICATION)')
                del w[m]; neighbor_update(m)
            elif b.kind == 'rail' and not sturdy(w.get(add(m, DOWN))):
                problems.append(f'rail en {m} casse (support absent)'); del w[m]; neighbor_update(m)
    for k in range(len(tp)-1, -1, -1):
        p = tp[k]; dest = add(p, d)
        vacated.discard(dest)
        w[dest] = B('moving', state=states[k], order=len(order))
        order.append((dest, states[k], p))
        log.append(f'  traite #{k} {states[k].kind:13s} {p} -> {dest}')
        # mises a jour de forme autour de dest : les coraux dont le support est dest cassent
        for dd in DIRS:
            m = add(dest, dd); b = w.get(m)
            if b is not None and b.kind == 'fan' and add(m, b.p['support']) == dest:
                log.append(f'  ... corail en {m} casse (support {dest} devenu bloc 36)')
                del w[m]; neighbor_update(m)
    for v in vacated:
        if v in w: del w[v]
    if verbose: print('\n'.join(log))
    # arrivee (ordre de creation)
    for dest, st, src in order:
        w[dest] = st
        if st.kind == 'fan' and not sturdy(w.get(add(dest, st.p['support']))):
            problems.append(f'corail en {dest} detruit a l\'arrivee (support pas encore arrive)'); del w[dest]; continue
        if st.kind == 'rail' and not sturdy(w.get(add(dest, DOWN))):
            problems.append(f'rail en {dest} casse a l\'arrivee (support pas encore arrive)'); del w[dest]; continue
        if st.kind == 'tnt' and tnt_powered(w, dest):
            problems.append(f'TNT en {dest} amorcee A L\'ARRIVEE (consommee)'); del w[dest]; continue
        # mises a jour des voisins (flag 67) : seulement les 6 voisins du bloc arrive
        for dd in DIRS:
            m = add(dest, dd); b = w.get(m)
            if b is not None and b.kind == 'tnt' and tnt_powered(w, m):
                problems.append(f'TNT en {m} amorcee par l\'arrivee de {st.kind} en {dest} (consommee)'); del w[m]
    if verbose:
        print(f'  arrivee: ' + ', '.join(f'{s.kind}{dd}' for dd, s, _ in order))
        print(f'  => duplications: {dupes}  problemes: {problems}')
    return dupes, problems
