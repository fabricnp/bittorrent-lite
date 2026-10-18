"""
plot_results.py - Generate plots from experiment results.

Reads results.json and creates:
  1. Box plots of distribution time per strategy
  2. Per-peer completion time comparison
  3. Piece availability variance over time
  4. Free-rider experiment results
"""

import json
import os
import sys

import matplotlib.pyplot as plt
import pandas as pd
import numpy as np


def load_results(results_path='experiments/results/results.json'):
    with open(results_path) as f:
        return json.load(f)


def plot_distribution_time(results, output_dir='experiments/results'):
    """
    Box plot: total distribution time by strategy for each (swarm_size, seeder_count).
    """
    # filter out free-rider experiments
    main_results = [r for r in results if r.get('experiment_type') != 'free_rider']

    rows = []
    for r in main_results:
        cfg = r['config']
        leecher_times = []
        for p in r['peers']:
            if p['role'] == 'leecher' and p['complete']:
                leecher_times.append(p['elapsed_seconds'])

        if leecher_times:
            # total distribution time = time until ALL leechers finish
            total_time = max(leecher_times)
            total_peers = cfg['num_seeders'] + cfg['num_leechers']
            rows.append({
                'strategy': cfg['strategy'],
                'swarm_size': total_peers,
                'seeders': cfg['num_seeders'],
                'run': r.get('run', 0),
                'total_time': total_time,
            })

    df = pd.DataFrame(rows)
    if df.empty:
        print("No data to plot")
        return

    # one subplot per (swarm_size, seeders) combo
    groups = df.groupby(['swarm_size', 'seeders'])
    n_groups = len(groups)
    fig, axes = plt.subplots(1, n_groups, figsize=(5 * n_groups, 6), sharey=True)
    if n_groups == 1:
        axes = [axes]

    for ax, ((swarm, seeds), group_df) in zip(axes, groups):
        data = []
        labels = []
        for strategy in ['rarest_first', 'random', 'sequential']:
            s_data = group_df[group_df['strategy'] == strategy]['total_time'].values
            data.append(s_data)
            labels.append(strategy.replace('_', '\n'))

        bp = ax.boxplot(data, labels=labels, patch_artist=True)
        colors = ['#4CAF50', '#2196F3', '#FF9800']
        for patch, color in zip(bp['boxes'], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.7)

        ax.set_title(f'Swarm={swarm}, Seeders={seeds}')
        ax.set_ylabel('Distribution Time (s)')
        ax.grid(True, alpha=0.3)

    fig.suptitle('Total Distribution Time by Piece Selection Strategy', fontsize=14)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'distribution_time.png'), dpi=150)
    plt.close()
    print("Saved distribution_time.png")


def plot_per_peer_completion(results, output_dir='experiments/results'):
    """
    Box plot of individual peer completion times.
    """
    main_results = [r for r in results if r.get('experiment_type') != 'free_rider']

    rows = []
    for r in main_results:
        cfg = r['config']
        total_peers = cfg['num_seeders'] + cfg['num_leechers']
        for p in r['peers']:
            if p['role'] == 'leecher':
                rows.append({
                    'strategy': cfg['strategy'],
                    'swarm_size': total_peers,
                    'seeders': cfg['num_seeders'],
                    'peer_id': p['peer_id'],
                    'elapsed': p['elapsed_seconds'],
                    'complete': p['complete'],
                })

    df = pd.DataFrame(rows)
    if df.empty:
        return

    fig, axes = plt.subplots(1, 2, figsize=(12, 6))

    for ax, swarm_size in zip(axes, [6, 8]):
        sub = df[df['swarm_size'] == swarm_size]
        data = []
        labels = []
        for s in ['rarest_first', 'random', 'sequential']:
            vals = sub[sub['strategy'] == s]['elapsed'].values
            data.append(vals)
            labels.append(s.replace('_', '\n'))

        bp = ax.boxplot(data, labels=labels, patch_artist=True)
        colors = ['#4CAF50', '#2196F3', '#FF9800']
        for patch, c in zip(bp['boxes'], colors):
            patch.set_facecolor(c)
            patch.set_alpha(0.7)

        ax.set_title(f'Swarm Size = {swarm_size}')
        ax.set_ylabel('Per-Peer Completion Time (s)')
        ax.grid(True, alpha=0.3)

    fig.suptitle('Per-Peer Completion Time by Strategy', fontsize=14)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'per_peer_completion.png'), dpi=150)
    plt.close()
    print("Saved per_peer_completion.png")


def plot_free_rider_results(results, output_dir='experiments/results'):
    """
    Compare completion times of free-riders vs normal peers.
    """
    fr_results = [r for r in results if r.get('experiment_type') == 'free_rider']

    if not fr_results:
        print("No free-rider results to plot")
        return

    normal_times = []
    freerider_times = []

    for r in fr_results:
        for p in r['peers']:
            if p['role'] != 'leecher':
                continue
            if '-FR' in p['peer_id']:
                freerider_times.append(p['elapsed_seconds'])
            else:
                normal_times.append(p['elapsed_seconds'])

    fig, ax = plt.subplots(figsize=(8, 6))

    data = [normal_times, freerider_times]
    bp = ax.boxplot(data, labels=['Normal Peers', 'Free Riders'], patch_artist=True)
    bp['boxes'][0].set_facecolor('#4CAF50')
    bp['boxes'][0].set_alpha(0.7)
    bp['boxes'][1].set_facecolor('#F44336')
    bp['boxes'][1].set_alpha(0.7)

    ax.set_ylabel('Completion Time (s)')
    ax.set_title('Impact of Choking on Free-Riders')
    ax.grid(True, alpha=0.3)

    # add text annotations
    if normal_times and freerider_times:
        avg_normal = np.mean(normal_times)
        avg_fr = np.mean(freerider_times)
        ax.text(0.02, 0.98, f'Avg Normal: {avg_normal:.1f}s\nAvg Free-Rider: {avg_fr:.1f}s',
                transform=ax.transAxes, va='top', fontsize=10,
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'free_rider_impact.png'), dpi=150)
    plt.close()
    print("Saved free_rider_impact.png")


def generate_summary_table(results, output_dir='experiments/results'):
    """Create a summary CSV with mean/std for each config."""
    main_results = [r for r in results if r.get('experiment_type') != 'free_rider']

    rows = []
    for r in main_results:
        cfg = r['config']
        total_peers = cfg['num_seeders'] + cfg['num_leechers']
        leecher_times = [p['elapsed_seconds'] for p in r['peers']
                        if p['role'] == 'leecher' and p['complete']]
        if leecher_times:
            rows.append({
                'strategy': cfg['strategy'],
                'swarm_size': total_peers,
                'seeders': cfg['num_seeders'],
                'run': r.get('run', 0),
                'max_time': max(leecher_times),
                'mean_time': np.mean(leecher_times),
                'std_time': np.std(leecher_times),
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return

    summary = df.groupby(['strategy', 'swarm_size', 'seeders']).agg(
        avg_distribution_time=('max_time', 'mean'),
        std_distribution_time=('max_time', 'std'),
        avg_peer_time=('mean_time', 'mean'),
    ).round(3).reset_index()

    csv_path = os.path.join(output_dir, 'summary.csv')
    summary.to_csv(csv_path, index=False)
    print(f"Saved {csv_path}")
    print(summary.to_string())


def main():
    results_path = sys.argv[1] if len(sys.argv) > 1 else 'experiments/results/results.json'

    if not os.path.exists(results_path):
        print(f"No results found at {results_path}")
        print("Run the experiments first: python -m experiments.experiment")
        return

    results = load_results(results_path)
    output_dir = os.path.dirname(results_path)

    plot_distribution_time(results, output_dir)
    plot_per_peer_completion(results, output_dir)
    plot_free_rider_results(results, output_dir)
    generate_summary_table(results, output_dir)

    print("\nAll plots generated!")


if __name__ == '__main__':
    main()
