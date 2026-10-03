#!/usr/bin/env python3
"""Contour graph + in-silico lesion (knock-out) sweep. stdlib only, deterministic, no I/O besides params.json.

Idea borrowed from connectome work (structure first, then silence nodes and watch which outputs survive), but the
mathematics here is classical reliability analysis (RBD / FMEA / minimal cut sets). See docs/SIM-BIOMIMETIC.md.
Fault modes: "dead" (node removed) and "stuck" (node stays alive and keeps asserting stale output; only for
stuck_capable nodes). A stuck writer MASKS the FC radio failsafe (docs/SIM-BLOCKERS.md 3.1).
This is a MODEL of the author's dataflow assumptions (params.json tags), not a measurement.
"""
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FUNCS = ("video", "telemetry", "rc_primary", "rc_backup", "control", "safe_failsafe")


def load(path=None):
    with open(path or os.path.join(HERE, "params.json")) as f:
        return json.load(f)


def alive_map(P, dead):
    """Effective liveness: not knocked out AND every 'needs' group (any-of) has a live member. Needs must be acyclic."""
    nodes = P["nodes"]
    memo, busy = {}, set()

    def alive(n):
        if n in memo:
            return memo[n]
        if n in busy:
            raise ValueError("cyclic needs at " + n)
        busy.add(n)
        ok = n not in dead and all(any(alive(m) for m in grp) for grp in nodes[n]["needs"])
        busy.discard(n)
        memo[n] = ok
        return ok

    return {n: alive(n) for n in nodes}


def evaluate(P, faults):
    """faults: iterable of (node, mode). Returns {function: bool} (True = function survives)."""
    faults = list(faults)
    dead = {n for n, m in faults if m == "dead"}
    stuck = {n for n, m in faults if m == "stuck"}
    al = alive_map(P, dead)
    res = {}
    for name, fn in P["functions"].items():
        res[name] = any(all(al[n] for n in path) for path in fn["paths"])
    res["control"] = res["rc_primary"] or res["rc_backup"]
    # safe failsafe: the FC is the actuator of every failsafe; it works if the FC lives and no live stuck writer feeds it
    masked = False
    for s in stuck:
        if not al[s]:
            continue
        for key in ("rc_primary", "rc_backup"):
            for path in P["functions"][key]["paths"]:
                if s in path:
                    tail = path[path.index(s):]
                    if all(al[n] for n in tail):
                        masked = True
    res["safe_failsafe"] = al["fc"] and not masked
    return res


def universe(P):
    u = [(n, "dead") for n in P["nodes"]]
    u += [(n, "stuck") for n, d in P["nodes"].items() if d.get("stuck_capable")]
    return u


def score(P, res, uniform=False):
    w = P["scoring_weights"]
    return sum((1 if uniform else w[f]) for f in FUNCS if not res[f])


def single_sweep(P):
    rows = []
    for f in universe(P):
        r = evaluate(P, [f])
        rows.append((f, r, score(P, r), score(P, r, True)))
    rows.sort(key=lambda x: (-x[2], -x[3], x[0]))
    return rows


def pair_sweep(P):
    """All pairs of faults on DISTINCT nodes. Returns rows and the minimal 2-cut sets of 'control' and 'safe_failsafe'."""
    singles = {f: evaluate(P, [f]) for f in universe(P)}
    rows, synergy = [], []
    for a, b in itertools.combinations(universe(P), 2):
        if a[0] == b[0]:
            continue
        r = evaluate(P, [a, b])
        rows.append(((a, b), r, score(P, r)))
        for fn in ("control", "safe_failsafe"):
            if not r[fn] and singles[a][fn] and singles[b][fn]:
                synergy.append((fn, a, b))
    return rows, synergy


def betweenness(P):
    """Brandes betweenness on the directed union graph: data edges of all function paths plus supplier->dependent
    'needs' edges (so power/host nodes are not artificially at zero)."""
    adj = {n: set() for n in P["nodes"]}
    for n, d in P["nodes"].items():
        for grp in d["needs"]:
            for m in grp:
                adj[m].add(n)
    for fn in P["functions"].values():
        for path in fn["paths"]:
            for u, v in zip(path, path[1:]):
                adj[u].add(v)
    bc = {n: 0.0 for n in adj}
    for s in sorted(adj):
        stack, pred = [], {n: [] for n in adj}
        sigma = {n: 0 for n in adj}
        sigma[s] = 1
        dist = {n: -1 for n in adj}
        dist[s] = 0
        q = [s]
        while q:
            v = q.pop(0)
            stack.append(v)
            for w in sorted(adj[v]):
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    q.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = {n: 0.0 for n in adj}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s:
                bc[w] += delta[w]
    return bc


def ranks(vals):
    order = sorted(vals, key=lambda k: (-vals[k], k))
    # average ranks for ties
    r, i = {}, 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in order[i:j + 1]:
            r[k] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    ks = sorted(a)
    n = len(ks)
    ma = sum(ra[k] for k in ks) / n
    mb = sum(rb[k] for k in ks) / n
    cov = sum((ra[k] - ma) * (rb[k] - mb) for k in ks)
    va = sum((ra[k] - ma) ** 2 for k in ks) ** 0.5
    vb = sum((rb[k] - mb) ** 2 for k in ks) ** 0.5
    return cov / (va * vb) if va and vb else 0.0


def node_lesion_score(P, rows, uniform=False):
    """Per-node score = dead-mode single-lesion score."""
    return {f[0]: (u if uniform else w) for f, _r, w, u in rows if f[1] == "dead"}


def human_nodes(P):
    s = set()
    for k, v in P["human_list"].items():
        if not k.startswith("_"):
            s.update(v)
    return s


def report_single(P):
    rows = single_sweep(P)
    out = ["# single knock-out sweep: fault, surviving functions (1=ok), weighted score (SYNTH weights), uniform score"]
    out.append("%-12s %-5s " % ("node", "mode") + " ".join("%-6s" % f[:6] for f in FUNCS) + "  w  u")
    for (n, m), r, w, u in rows:
        out.append("%-12s %-5s " % (n, m) + " ".join("%-6d" % int(r[f]) for f in FUNCS) + " %2d %2d" % (w, u))
    return "\n".join(out) + "\n"


def report_pairs(P):
    rows, syn = pair_sweep(P)
    singles = {f: evaluate(P, [f]) for f in universe(P)}
    out = ["# double knock-out sweep over distinct nodes"]
    out.append("fault universe: %d (node,mode) faults, pairs on distinct nodes: %d" % (len(universe(P)), len(rows)))
    for fn in FUNCS:
        n = sum(1 for _p, r, _s in rows if not r[fn])
        out.append("pairs losing %-13s %4d" % (fn, n))
    ctl = sorted(s for s in syn if s[0] == "control")
    out.append("minimal 2-cut sets of control (neither single kills it): %d" % len(ctl))
    sf = sorted(s for s in syn if s[0] == "safe_failsafe")
    out.append("minimal 2-cut sets of safe_failsafe: %d" % len(sf))
    for _fn, a, b in sf:
        out.append("  safe_failsafe: %s/%s + %s/%s" % (a + b))
    only1 = sorted(f for f, r in singles.items() if not r["control"])
    out.append("single points of failure of control (common cause of both RC paths): " + ", ".join("%s/%s" % f for f in only1))
    # which nodes of the primary and backup RC paths form the cut-set grid
    prim = {n for n in P["functions"]["rc_primary"]["paths"][0]}
    back = {n for n in P["functions"]["rc_backup"]["paths"][0]}
    out.append("nodes only on primary RC path: " + ", ".join(sorted(prim - back)))
    out.append("nodes only on backup RC path: " + ", ".join(sorted(back - prim)))
    return "\n".join(out) + "\n"


def report_rank(P):
    rows = single_sweep(P)
    dead = node_lesion_score(P, rows)
    deadu = node_lesion_score(P, rows, True)
    bc = betweenness(P)
    hum = human_nodes(P)
    top = sorted(dead, key=lambda k: (-dead[k], -deadu[k], k))
    out = ["# criticality ranking (dead mode): lesion score vs betweenness vs human list"]
    out.append("%-12s %-4s %-4s %-8s %s" % ("node", "w", "u", "betw", "in_human_list"))
    for n in top:
        out.append("%-12s %-4d %-4d %-8.1f %s" % (n, dead[n], deadu[n], bc[n], "yes" if n in hum else "NO"))
    out.append("spearman(lesion_w, betweenness) = %.3f" % spearman(dead, bc))
    out.append("spearman(lesion_u, betweenness) = %.3f" % spearman(deadu, bc))
    out.append("top-3 by weighted lesion score: " + ", ".join(top[:3]))
    out.append("top-3 by betweenness:          " + ", ".join(sorted(bc, key=lambda k: (-bc[k], k))[:3]))
    mx = max(dead.values())
    tied = [n for n in top if dead[n] == mx]
    out.append("max weighted score %d reached by %d nodes: %s" % (mx, len(tied), ", ".join(tied)))
    miss = [n for n in top if dead[n] >= mx and n not in hum]
    out.append("max-score nodes NOT in the human list: " + (", ".join(miss) or "none"))
    stuck = [(f, w) for f, _r, w, _u in rows if f[1] == "stuck"]
    out.append("stuck-mode scores: " + ", ".join("%s=%d" % (f[0], w) for f, w in stuck))
    return "\n".join(out) + "\n"


def main():
    P = load()
    sys.stdout.write(report_single(P) + report_pairs(P) + report_rank(P))


if __name__ == "__main__":
    main()
