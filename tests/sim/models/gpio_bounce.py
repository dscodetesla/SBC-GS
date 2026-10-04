#!/usr/bin/env python3
"""GPIO switch bounce generator + replay of the REAL press-length algorithm of gs/button.sh (button_action), stdlib only.

The algorithm (REPO gs/button.sh, function button_action), as modelled step by step:
    gpiomon -r -s -n 1 -B pull-down PIN     # blocks until the FIRST rising edge (press, active level 1)
    sleep 0.05; [ "$(gpioget PIN)" = 1 ] || continue     # the only debounce: level must still be 1 after 50 ms
    t0 = /proc/uptime in hundredths (cut -d ' ' -f1 | tr -d .)   # taken AFTER the settle delay, not at the edge
    gpiomon -f -s -n 1 -B pull-down PIN     # blocks until the FIRST falling edge, a NEW process: edges before it starts are missed
    t1 = /proc/uptime in hundredths
    t1 - t0 < 200 -> "single", else "long"  # 200 = 2 s threshold in centiseconds
Consequences the replay exposes (not asserted by the shell script): the measured length is shorter than the physical hold by the
settle delay + process latencies (~0.07 s), so the effective long threshold is ~2.07 s; chatter during a hold ends the measurement
early (false single) and a later rising edge starts a second measurement; a release bounce longer than the settle delay re-arms.

Event schema "sbc-gs-gpio-bounce/1" (JSON, one trace per press; consumed later by tests/sim/virt to drive gpio-sim):
  {"schema": ..., "scenario": str, "seed": int, "line": {"pull": "down", "active_level": 1, "idle_level": 0},
   "intent": {"kind": "single"|"long", "press_at_us": int, "hold_us": int},
   "events": [{"t_us": int, "level": 0|1, "cause": "press"|"press_bounce"|"release"|"release_bounce"|"glitch"|"chatter"}],   # sorted by t_us, level changes only
   "algorithm": {"settle_s": .., "long_threshold_cs": .., "debounce_ms": ..},
   "expected_actions": ["single"|"long", ...]}     # what the replayed gs/button.sh algorithm outputs for this trace
Times are microseconds from the start of the trace; the gpio-sim writer sets the line level at each t_us.
Every parameter is in params.degrade.json section gpio (RP1 ones UNMEASURED); override as in priors.py.
"""
import math

from priors import Rng

IDLE, ACTIVE = 0, 1


def gen_trace(th, rng, hold_s, t_press=1.0, tail_s=3.0):
    """Edges [(t_s, level, cause)] for one press of hold_s seconds, with bounces, chatter and idle EMI glitches."""
    g = th.get
    ev = []
    nb = 1 + int(round(max(0.0, rng.lognorm(g("gpio.bounce_count_mean"), 0.3) - 1.0)))
    d_ms = rng.lognorm(g("gpio.bounce_total_ms"), 0.5)

    def bounce(t0, ms, n, final, cause_first, cause_b):
        out = [(t0, final, cause_first)]
        t = t0
        lvl = final
        for _ in range(2 * n):
            t += rng.expo(ms / 1000.0 / (2 * n))
            lvl = 1 - lvl
            out.append((t, lvl, cause_b))
        if lvl != final:  # settle at the final level
            t += rng.expo(ms / 1000.0 / (2 * n))
            out.append((t, final, cause_b))
        return out

    ev += bounce(t_press, d_ms, nb, ACTIVE, "press", "press_bounce")
    # the contact bounce above alternates starting with the first edge level; fix levels so the line goes 0->1 first then 1,0,1,...
    ev = _fix_levels(ev, ACTIVE)
    t_rel = t_press + hold_s
    if hold_s > 0.5 and rng.u() < g("gpio.chatter_prob"):
        tc = t_press + 0.2 + rng.u() * (hold_s - 0.4)
        ev += [(tc, IDLE, "chatter"), (tc + rng.lognorm(g("gpio.chatter_ms"), 0.4) / 1000.0, ACTIVE, "chatter")]
    rb_ms = d_ms * g("gpio.release_bounce_scale")
    rel = bounce(t_rel, rb_ms, nb, IDLE, "release", "release_bounce")
    ev += _fix_levels(rel, IDLE)
    end = t_rel + tail_s
    rate = g("gpio.glitch_rate_per_s")
    t = 0.0
    while rate > 0:
        t += rng.expo(1.0 / rate)
        if t >= end:
            break
        w = rng.lognorm(g("gpio.glitch_width_us"), 0.5) * 1e-6
        ev += [(t, 1 - _level_at(ev, t), "glitch"), (t + w, _level_at(ev, t), "glitch")]
    return _normalise(ev), end


def _fix_levels(seq, final):
    """Bounce sequences alternate; make the first transition leave the opposite of `final` and the last reach `final`."""
    n = len(seq)
    out = []
    for i, (t, _l, c) in enumerate(seq):
        lvl = final if (n - 1 - i) % 2 == 0 else 1 - final
        out.append((t, lvl, c))
    return out


def _level_at(ev, t):
    lvl = IDLE
    for te, l, _c in sorted(ev, key=lambda x: x[0]):
        if te <= t:
            lvl = l
        else:
            break
    return lvl


def _normalise(ev):
    """Sort by time, drop events that do not change the level (a glitch inside a bounce can duplicate levels)."""
    ev = sorted(ev, key=lambda x: x[0])
    out, lvl = [], IDLE
    for t, l, c in ev:
        if l != lvl:
            out.append((t, l, c))
            lvl = l
    return out


def debounce_filter(trace, window_s):
    """Debounce / glitch filter: an edge is reported only if the level then stays for window_s; the report is delayed by window_s."""
    if window_s <= 0:
        return list(trace)
    out, lvl = [], IDLE
    for i, (t, l, c) in enumerate(trace):
        nxt = trace[i + 1][0] if i + 1 < len(trace) else math.inf
        if l != lvl and nxt - t >= window_s:
            out.append((t + window_s, l, c))
            lvl = l
    return out


def replay_button_sh(trace, th, rng, t_end):
    """Replay gs/button.sh button_action over an edge trace -> [(t_s, "single"|"long")]."""
    g = th.get
    win = max(g("gpio.debounce_ms") / 1000.0, g("gpio.rp1_filter_us") * 1e-6)
    lat = g("gpio.rp1_irq_latency_us") * 1e-6
    loss = g("gpio.rp1_edge_loss_prob")
    edges = [(t + lat, l, c) for (t, l, c) in debounce_filter(trace, win) if rng.u() >= loss]
    settle, thr = g("gpio.settle_s"), int(g("gpio.long_threshold_cs"))

    def level_at(t):
        lvl = IDLE
        for te, l, _c in edges:
            if te <= t:
                lvl = l
            else:
                break
        return lvl

    def first_edge(t_arm, level):
        for te, l, _c in edges:
            if te >= t_arm and l == level:
                return te
        return None

    t, out = 0.0, []
    while t < t_end:
        e = first_edge(t + g_ms(g, rng, "gpio.mon_start_ms"), ACTIVE)
        if e is None:
            break
        t = e + g_ms(g, rng, "gpio.exit_ms") + settle + 0.001
        t += g_ms(g, rng, "gpio.get_ms")
        if level_at(t) != ACTIVE:
            continue
        t += g_ms(g, rng, "gpio.uptime_ms")
        press_cs = int(t * 100)
        e2 = first_edge(t + g_ms(g, rng, "gpio.mon_start_ms"), IDLE)
        if e2 is None:
            break  # still held at the end of the trace
        t = e2 + g_ms(g, rng, "gpio.exit_ms") + g_ms(g, rng, "gpio.uptime_ms")
        out.append((t, "single" if int(t * 100) - press_cs < thr else "long"))
        t += g_ms(g, rng, "gpio.action_ms")
    return out


def g_ms(g, rng, key):
    return rng.lognorm(g(key), 0.15) / 1000.0


def hold_for(th, rng, kind):
    g = th.get
    if kind == "single":
        return rng.lognorm(g("gpio.single_hold_s"), 0.35)
    return max(0.5, g("gpio.long_hold_mu_s") + g("gpio.long_hold_sigma_s") * rng.z())


def button_stats(th, rng, n_each=40):
    """Monte-Carlo over presses with the sampled parameters. Returns probabilities (each in [0,1])."""
    c = {"single_ok": 0, "single_extra": 0, "single_missed": 0, "single_as_long": 0,
         "long_ok": 0, "long_as_single": 0, "long_extra": 0, "long_missed": 0}
    for kind in ("single", "long"):
        for _ in range(n_each):
            hold = hold_for(th, rng, kind)
            trace, end = gen_trace(th, rng, hold)
            acts = [a for _t, a in replay_button_sh(trace, th, rng, end + 5.0)]
            if kind == "single":
                if not acts:
                    c["single_missed"] += 1
                elif "long" in acts:
                    c["single_as_long"] += 1
                elif len(acts) > 1:
                    c["single_extra"] += 1
                else:
                    c["single_ok"] += 1
            else:
                if not acts:
                    c["long_missed"] += 1
                elif acts == ["long"]:
                    c["long_ok"] += 1
                elif "single" in acts and "long" not in acts and len(acts) == 1:
                    c["long_as_single"] += 1
                else:
                    c["long_extra"] += 1
    p = {k: v / n_each for k, v in c.items()}
    p["false_event_single"] = 1.0 - p["single_ok"]
    p["false_event_long"] = 1.0 - p["long_ok"]
    p["false_event"] = 0.5 * (p["false_event_single"] + p["false_event_long"])
    return p


def trace_json(th, scenario, seed, kind="long", hold_s=None):
    """JSON-able bounce trace + the expected actions of the replayed algorithm (see the module docstring)."""
    rng = Rng(seed)
    hold = hold_s if hold_s is not None else hold_for(th, rng, kind)
    trace, end = gen_trace(th, rng, hold)
    acts = [a for _t, a in replay_button_sh(trace, th, Rng(seed + 1), end + 5.0)]
    return {"schema": "sbc-gs-gpio-bounce/1", "scenario": scenario, "seed": seed,
            "line": {"pull": "down", "active_level": ACTIVE, "idle_level": IDLE},
            "intent": {"kind": kind, "press_at_us": 1000000, "hold_us": int(round(hold * 1e6))},
            "events": [{"t_us": int(round(t * 1e6)), "level": l, "cause": c} for t, l, c in trace],
            "algorithm": {"settle_s": th.get("gpio.settle_s"), "long_threshold_cs": int(th.get("gpio.long_threshold_cs")),
                          "debounce_ms": th.get("gpio.debounce_ms")},
            "expected_actions": acts}
