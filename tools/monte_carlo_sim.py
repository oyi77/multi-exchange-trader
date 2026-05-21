#!/usr/bin/env python3
"""
Monte Carlo Strategy Simulator — Project Arbiter
Simulates thousands of random market scenarios to estimate
probability distributions of outcomes for Deriv DIGITMATCH (SNIPER MODE).

Usage: python3 scripts/monte_carlo_sim.py
"""

import random, json, math
from pathlib import Path
from collections import Counter

# =========== STRATEGY PARAMETERS (from SNIPER MODE) ===========
CONFIG = {
    "strategy_name": "SNIPER MODE — Cold Digit + DIGITMATCH",
    "markets": 5,               # 5 parallel markets
    "stake": 1.0,               # $1 per shot
    "payout": 8.33,             # DIGITMATCH payout
    "profit_per_win": 7.33,     # $8.33 - $1
    "win_rate": 0.10,           # 10% (true RNG for any digit)
    "max_shots": 8,             # Max shots per sequence (Sniper)
    "daily_tp": 15.0,           # Take profit target
    "daily_sl": -10.0,          # Stop loss limit
    "starting_capital": 50.0,   # Starting balance
    "sequences_per_day": 20,    # Max sequences per day (5 markets × ~4 cold events)
    "trading_days": 30,         # Simulation period
}

# =========== MONTE CARLO ENGINE ===========
class MonteCarloEngine:
    def __init__(self, config, seed=42):
        self.cfg = config
        self.rng = random.Random(seed)
    
    def run_sequence(self):
        """Simulate one SNIPER sequence: fire up to 8 shots, stop on win.
        Returns: (pnl, shots_fired, won)"""
        shots_fired = 0
        won = False
        
        for _ in range(self.cfg["max_shots"]):
            shots_fired += 1
            # Random tick: does predicted digit match?
            if self.rng.random() < self.cfg["win_rate"]:
                # WIN! Hit & Run — stop immediately
                won = True
                pnl = self.cfg["profit_per_win"] - (shots_fired - 1) * self.cfg["stake"]
                return (pnl, shots_fired, True)
        
        # All shots missed
        pnl = -shots_fired * self.cfg["stake"]
        return (pnl, shots_fired, False)
    
    def run_day(self):
        """Simulate one trading day: multiple sequences until TP/SL hit."""
        capital = 0.0  # Day's PnL starts at 0
        total_shots = 0
        sequences_run = 0
        wins = 0
        losses = 0
        max_dd = 0.0
        peak = 0.0
        
        for _ in range(self.cfg["sequences_per_day"] * self.cfg["markets"]):
            # Check daily limits
            if capital >= self.cfg["daily_tp"]:
                break
            if capital <= self.cfg["daily_sl"]:
                break
            
            sequences_run += 1
            pnl, shots, won = self.run_sequence()
            capital += pnl
            total_shots += shots
            
            if won:
                wins += 1
            else:
                losses += 1
            
            if capital > peak:
                peak = capital
            dd = (peak - capital) / (self.cfg["starting_capital"] + peak) * 100 if (self.cfg["starting_capital"] + peak) > 0 else 0
            if dd > max_dd:
                max_dd = dd
        
        return {
            "pnl": round(capital, 2),
            "shots": total_shots,
            "sequences": sequences_run,
            "wins": wins,
            "losses": losses,
            "max_drawdown_pct": round(max_dd, 1)
        }
    
    def run_full_simulation(self, days=None):
        """Run N days of trading."""
        if days is None:
            days = self.cfg["trading_days"]
        
        capital = self.cfg["starting_capital"]
        peak = capital
        max_dd = 0.0
        total_shots = 0
        total_sequences = 0
        total_wins = 0
        total_losses = 0
        daily_results = []
        
        for day in range(1, days + 1):
            day_result = self.run_day()
            
            capital += day_result["pnl"]
            total_shots += day_result["shots"]
            total_sequences += day_result["sequences"]
            total_wins += day_result["wins"]
            total_losses += day_result["losses"]
            
            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
            
            daily_results.append({
                "day": day,
                "pnl": day_result["pnl"],
                "balance": round(capital, 2),
                "shots": day_result["shots"],
                "wins": day_result["wins"],
                "losses": day_result["losses"],
            })
            
            # Ruin check
            if capital <= 0:
                break
        
        win_rate = total_wins / (total_wins + total_losses) * 100 if (total_wins + total_losses) > 0 else 0
        
        return {
            "final_capital": round(capital, 2),
            "total_pnl": round(capital - self.cfg["starting_capital"], 2),
            "return_pct": round((capital - self.cfg["starting_capital"]) / self.cfg["starting_capital"] * 100, 1),
            "peak_capital": round(peak, 2),
            "max_drawdown_pct": round(max_dd, 1),
            "total_shots": total_shots,
            "total_sequences": total_sequences,
            "win_rate_pct": round(win_rate, 1),
            "ruined": capital <= 0,
            "days_survived": len(daily_results),
            "daily_results": daily_results
        }


# =========== MONTE CARLO SIMULATOR (10K runs) ===========
def run_monte_carlo(config, num_simulations=10000):
    """Run N independent Monte Carlo simulations."""
    print(f"{'='*60}")
    print(f"📊 MONTE CARLO SIMULATION")
    print(f"   Strategy: {config['strategy_name']}")
    print(f"   Simulations: {num_simulations:,}")
    print(f"   Starting capital: \${config['starting_capital']}")
    print(f"   Trading days: {config['trading_days']}")
    print(f"   Win rate per shot: {config['win_rate']*100:.0f}%")
    print(f"   Profit per win: \${config['profit_per_win']}")
    print(f"   Daily TP: \${config['daily_tp']} | SL: -\${abs(config['daily_sl'])}")
    print(f"{'='*60}")
    
    results = []
    profits = []
    ruined_count = 0
    profitable_count = 0
    drawdowns = []
    win_rates = []
    
    for sim in range(num_simulations):
        engine = MonteCarloEngine(config, seed=sim)
        result = engine.run_full_simulation()
        
        results.append(result)
        profits.append(result["total_pnl"])
        drawdowns.append(result["max_drawdown_pct"])
        win_rates.append(result["win_rate_pct"])
        
        if result["ruined"]:
            ruined_count += 1
        if result["total_pnl"] > 0:
            profitable_count += 1
        
        if (sim + 1) % 2500 == 0:
            print(f"  🏃 {sim+1:,}/{num_simulations:,} simulations...")
    
    # Statistics
    profits.sort()
    drawdowns.sort()
    
    median_profit = profits[len(profits)//2]
    p5_profit = profits[int(len(profits)*0.05)]
    p95_profit = profits[int(len(profits)*0.95)]
    mean_profit = sum(profits) / len(profits)
    
    median_dd = drawdowns[len(drawdowns)//2]
    p95_dd = drawdowns[int(len(drawdowns)*0.95)]
    
    avg_win_rate = sum(win_rates) / len(win_rates)
    
    print(f"\n{'='*60}")
    print(f"📈 MONTE CARLO RESULTS ({num_simulations:,} simulations)")
    print(f"{'='*60}")
    print(f"")
    print(f"  🎯 OUTCOME DISTRIBUTION:")
    print(f"     Median profit:     \${median_profit:>+7.2f}")
    print(f"     Mean profit:       \${mean_profit:>+7.2f}")
    print(f"     5th percentile:    \${p5_profit:>+7.2f}")
    print(f"     95th percentile:   \${p95_profit:>+7.2f}")
    print(f"")
    print(f"  💀 RISK METRICS:")
    print(f"     Probability of profit:  {profitable_count/num_simulations*100:.1f}%")
    print(f"     Probability of ruin:    {ruined_count/num_simulations*100:.1f}%")
    print(f"     Median max drawdown:    {median_dd:.1f}%")
    print(f"     95th pct drawdown:      {p95_dd:.1f}%")
    print(f"")
    print(f"  📊 STRATEGY METRICS:")
    print(f"     Avg win rate:           {avg_win_rate:.1f}%")
    print(f"     Expected value/shot:    \${config['profit_per_win']*config['win_rate'] - config['stake']*(1-config['win_rate']):>+.2f}")
    print(f"     House edge:             {100 - (config['profit_per_win']+config['stake'])/config['stake']*config['win_rate']*100:.1f}%")
    print(f"")
    
    # Profit distribution histogram (text)
    print(f"  📊 PROFIT DISTRIBUTION (10 bins):")
    min_p = min(profits)
    max_p = max(profits)
    bin_size = (max_p - min_p) / 10 if max_p > min_p else 1
    bins = []
    for i in range(10):
        lo = min_p + i * bin_size
        hi = lo + bin_size
        count = sum(1 for p in profits if lo <= p < hi)
        bins.append((lo, hi, count))
    
    max_count = max(b[2] for b in bins) if bins else 1
    for lo, hi, count in bins:
        bar = "█" * int(count / max_count * 40)
        label = f"\${lo:+.0f} to \${hi:+.0f}"
        print(f"  {label:<20} {count:>5} ({count/num_simulations*100:>5.1f}%) {bar}")
    
    # Summary verdict
    profitable_pct = profitable_count / num_simulations * 100
    ruin_pct = ruined_count / num_simulations * 100
    
    print(f"\n{'='*60}")
    print(f"📋 FINAL VERDICT:")
    print(f"{'='*60}")
    
    if profitable_pct > 70 and ruin_pct < 5:
        print(f"  ✅ STRATEGY VIABLE — {profitable_pct:.0f}% chance of profit, {ruin_pct:.0f}% ruin risk")
    elif profitable_pct > 50 and ruin_pct < 15:
        print(f"  ⚠️ MODERATE — {profitable_pct:.0f}% chance of profit, {ruin_pct:.0f}% ruin risk")
    else:
        print(f"  ❌ HIGH RISK — {profitable_pct:.0f}% chance of profit, {ruin_pct:.0f}% ruin risk")
        print(f"     Strategy EV negative: \${mean_profit:.2f} average return")
    
    print(f"  Recommended max stake: \${config['stake']}")
    if config['max_shots'] > 3:
        print(f"  ⚠️ 8-shot sequence increases ruin probability vs 1-shot")
        print(f"  Consider: ONE SHOOT (1 shot) instead of {config['max_shots']}-shot sequence")
    
    print(f"{'='*60}")
    
    return {
        "profitable_pct": round(profitable_pct, 1),
        "ruin_pct": round(ruin_pct, 1),
        "median_profit": round(median_profit, 2),
        "mean_profit": round(mean_profit, 2),
        "p5_profit": round(p5_profit, 2),
        "p95_profit": round(p95_profit, 2),
        "median_drawdown": round(median_dd, 1),
        "avg_win_rate": round(avg_win_rate, 1),
    }


# =========== COMPARE STRATEGIES ===========
def compare_strategies():
    """Compare SNIPER MODE vs ONE SHOOT approach."""
    print(f"\n{'='*60}")
    print(f"📊 STRATEGY COMPARISON: SNIPER (8-shot) vs ONE SHOOT (1-shot)")
    print(f"{'='*60}")
    
    config = CONFIG.copy()
    
    # SNIPER MODE (8-shot)
    print(f"\n🔥 SNIPER MODE (8-shot max):")
    sniper = run_monte_carlo(config, num_simulations=5000)
    
    # ONE SHOOT (1-shot)
    config_one_shoot = config.copy()
    config_one_shoot["max_shots"] = 1
    config_one_shoot["strategy_name"] = "ONE SHOOT — Single DIGITMATCH"
    print(f"\n🎯 ONE SHOOT (1-shot):")
    one_shoot = run_monte_carlo(config_one_shoot, num_simulations=5000)
    
    # Comparison
    print(f"\n{'='*60}")
    print(f"📊 COMPARISON SUMMARY")
    print(f"{'='*60}")
    print(f"{'Metric':<30} {'SNIPER (8-shot)':<18} {'ONE SHOOT':<18}")
    print(f"{'-'*66}")
    print(f"{'Profit probability':<30} {sniper['profitable_pct']:>5.1f}%{'':<11} {one_shoot['profitable_pct']:>5.1f}%")
    print(f"{'Ruin probability':<30} {sniper['ruin_pct']:>5.1f}%{'':<11} {one_shoot['ruin_pct']:>5.1f}%")
    print(f"{'Median profit':<30} {'${:>+.2f}'.format(sniper['median_profit']):<18} {'${:>+.2f}'.format(one_shoot['median_profit']):<18}")
    print(f"{'Mean profit':<30} {'${:>+.2f}'.format(sniper['mean_profit']):<18} {'${:>+.2f}'.format(one_shoot['mean_profit']):<18}")
    print(f"{'5th percentile':<30} {'${:>+.2f}'.format(sniper['p5_profit']):<18} {'${:>+.2f}'.format(one_shoot['p5_profit']):<18}")
    print(f"{'95th percentile':<30} {'${:>+.2f}'.format(sniper['p95_profit']):<18} {'${:>+.2f}'.format(one_shoot['p95_profit']):<18}")
    print(f"{'Median max DD':<30} {sniper['median_drawdown']:>5.1f}%{'':<11} {one_shoot['median_drawdown']:>5.1f}%")
    
    return sniper, one_shoot


if __name__ == "__main__":
    import sys
    
    if "--compare" in sys.argv:
        compare_strategies()
    else:
        run_monte_carlo(CONFIG, num_simulations=10000)
    
    print(f"\n💾 Results interpretation:")
    print(f"  - If profit probability > 70% → strategy has edge")
    print(f"  - If ruin probability > 10% → too risky")
    print(f"  - Check 5th percentile worst case → can you survive that?")
