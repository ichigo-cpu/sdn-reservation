#!/usr/bin/env python3
"""Make PNG plots in results/ from the experiment CSVs.
Run (normal terminal, no sudo): python3 tests/plots.py
"""
import os, csv, statistics, collections
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'results')


def rd(name):
    with open(os.path.join(R, name)) as f:
        return list(csv.DictReader(f))


def save(fig_name):
    plt.tight_layout()
    plt.savefig(os.path.join(R, fig_name), dpi=150)
    plt.close()
    print('wrote', fig_name)


def plot_exp1():
    d = collections.OrderedDict()
    for r in rd('exp1_throughput.csv'):
        d.setdefault(r['condition'], []).append(float(r['throughput_mbps']))
    names = list(d)
    means = [statistics.mean(d[n]) for n in names]
    sds = [statistics.stdev(d[n]) if len(d[n]) > 1 else 0 for n in names]
    plt.figure(figsize=(9, 5))
    plt.bar(range(len(names)), means, yerr=sds, capsize=4, color='#4C78A8')
    for i, n in enumerate(names):
        plt.scatter([i] * len(d[n]), d[n], color='k', s=12, zorder=3)
    plt.xticks(range(len(names)), [n.replace('_', '\n') for n in names], fontsize=8)
    plt.ylabel('h1 -> h3 TCP throughput (Mbps)')
    plt.title('Experiment 1: throughput by condition (mean, sd, runs)')
    save('plot_exp1_throughput.png')


def plot_exp2():
    s = rd('exp2_load_summary.csv')
    x = [int(r['load']) for r in s]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.5))
    ax[0].plot(x, [float(r['reject_pct']) for r in s], 'o-', color='#E45756')
    ax[0].axvline(10, ls='--', color='gray')
    ax[0].text(10.3, 2, 'capacity = 10 requests', color='gray')
    ax[0].set_xlabel('offered load (RESERVE requests of 2 Mbps)')
    ax[0].set_ylabel('rejection rate (%)')
    ax[0].set_title('Rejection rate vs load')
    ax[1].plot(x, [float(r['mean_ms']) for r in s], 'o-', label='mean')
    ax[1].plot(x, [float(r['median_ms']) for r in s], 's-', label='median')
    ax[1].plot(x, [float(r['p95_ms']) for r in s], '^-', label='p95')
    ax[1].set_xlabel('offered load (RESERVE requests of 2 Mbps)')
    ax[1].set_ylabel('RESERVE latency (ms)')
    ax[1].set_title('Setup time vs load')
    ax[1].legend()
    save('plot_exp2_load.png')


def events():
    out = []
    with open(os.path.join(R, 'exp3_events.csv')) as f:
        for p in csv.reader(f):
            try:
                out.append((float(p[0]), p[1], p[2] if len(p) > 2 else ''))
            except (ValueError, IndexError):
                pass
    return out


def plot_exp3():
    rows = rd('exp3_failover.csv')
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.5))
    for cond in ('no_reservation', 'with_reservation'):
        by = collections.defaultdict(list)
        for r in rows:
            if r['condition'] == cond and int(r['second']) >= 2:
                by[int(r['second'])].append(float(r['mbps']))
        xs = sorted(by)
        ax[0].plot(xs, [statistics.mean(by[x]) for x in xs], 'o-', label=cond)
    ax[0].axvline(4, ls='--', color='r')
    ax[0].set_ylim(0, 11)
    ax[0].set_xlabel('time (s); red line = link s1-s2 down')
    ax[0].set_ylabel('throughput (Mbps, mean of 3 runs)')
    ax[0].set_title('Throughput across a link failure')
    ax[0].legend()
    ms = [float(d.split()[0]) for t, e, d in events() if e == 'FAILOVER_DONE'][-6:]
    ax[1].bar(range(len(ms)), ms, color=['#9ecae1'] * 3 + ['#3182bd'] * 3)
    ax[1].set_xticks(range(len(ms)))
    ax[1].set_xticklabels(['no res %d' % i for i in (1, 2, 3)] +
                          ['with res %d' % i for i in (1, 2, 3)], fontsize=8)
    ax[1].set_ylabel('LINK_DOWN to FAILOVER_DONE (ms)')
    ax[1].set_title('Controller failover time')
    save('plot_exp3_failover.png')


def plot_util():
    ev = events()
    downs = [t for t, e, d in ev if e == 'LINK_DOWN']
    ups = [t for t, e, d in ev if e == 'LINK_UP']
    t0, t1 = downs[0] - 6, ups[-1] + 4
    ser = {}
    for r in rd('utilization.csv'):
        t = float(r['time'])
        if t0 <= t <= t1 and r['link'] in ('s1->s2', 's1->s3'):
            ser.setdefault(r['link'], []).append((t - t0, float(r['tx_mbps'])))
    plt.figure(figsize=(11, 4.5))
    for l, v in ser.items():
        plt.plot(*zip(*v), 'o-', label=l + ' tx')
    for t in downs:
        plt.axvline(t - t0, color='r', ls='--', lw=0.8)
    for t in ups:
        plt.axvline(t - t0, color='g', ls=':', lw=0.8)
    plt.xlabel('time (s); red = link down, green = link up')
    plt.ylabel('link tx rate (Mbps)')
    plt.title('Link utilization during Experiment 3 failures')
    plt.legend()
    save('plot_utilization.png')


def main():
    plot_exp1()
    plot_exp2()
    plot_exp3()
    plot_util()


if __name__ == '__main__':
    main()
