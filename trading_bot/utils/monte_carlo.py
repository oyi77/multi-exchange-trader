import random


class MonteCarloRiskManager:
    """Monte Carlo simulation for risk of ruin estimation.

    Runs N simulations of trading outcomes based on historical win rate
    and average win/loss to estimate probability of ruin and expected returns.
    """

    def __init__(self, capital=100.0, trade_history=None):
        self.capital = capital
        self.history = trade_history or []
        self.last_sim_results = {}

    def update(self, capital, trade_history):
        self.capital = capital
        self.history = trade_history

    def get_stats(self):
        """Get win rate and avg PnL from recent trades."""
        recent = self.history[-50:] if len(self.history) > 50 else self.history
        if not recent:
            return {"win_rate": 0.45, "avg_win": 2.5, "avg_loss": -1.5, "trades": 0}

        wins = [t for t in recent if t > 0]
        losses = [t for t in recent if t < 0]

        wr = len(wins) / len(recent) if recent else 0.45
        avg_w = sum(wins) / len(wins) if wins else 2.5
        avg_l = sum(losses) / len(losses) if losses else -1.5

        return {
            "win_rate": round(wr, 3),
            "avg_win": round(avg_w, 2),
            "avg_loss": round(avg_l, 2),
            "trades": len(recent)
        }

    def simulate(self, num_simulations=500, days=7):
        """Run Monte Carlo and return risk metrics."""
        stats = self.get_stats()
        wr = stats["win_rate"]
        avg_w = stats["avg_win"]
        avg_l = abs(stats["avg_loss"])
        trades_per_day = max(1, stats["trades"] // max(1, len(self.history) // 7 if len(self.history) > 7 else 1))

        if wr < 0.001:
            return {"ruin_prob": 1.0, "profit_prob": 0.0, "recommended_max_risk": 0.01}

        rng = random.Random()
        outcomes = []
        ruined = 0
        profitable = 0

        for _ in range(num_simulations):
            bal = self.capital
            for _ in range(trades_per_day * days):
                if bal <= 0:
                    ruined += 1
                    break
                win = rng.random() < wr
                pnl = avg_w if win else -avg_l
                bal += pnl
            outcomes.append(bal)
            if bal > self.capital:
                profitable += 1

        outcomes.sort()
        median = outcomes[len(outcomes) // 2] if outcomes else self.capital

        return {
            "ruin_prob": round(ruined / num_simulations, 3),
            "profit_prob": round(profitable / num_simulations, 3),
            "median_balance": round(median, 2),
            "expected_return": round(
                (sum(outcomes) / len(outcomes) - self.capital) / self.capital * 100, 1
            ) if outcomes else 0,
            "recommended_max_stake_pct": 0.01 if ruined / num_simulations > 0.1 else 0.025,
            "num_simulations": num_simulations
        }

    def should_trade(self, ruin_threshold=0.15, profit_threshold=0.30, max_expected_loss=-5):
        """Returns (should_trade: bool, reason: str)."""
        sim = self.simulate(500, 3)
        self.last_sim_results = sim

        if sim["ruin_prob"] > ruin_threshold:
            return False, f"Ruin prob {sim['ruin_prob']*100:.0f}% > {ruin_threshold*100:.0f}% \u2014 STOP"
        if sim["profit_prob"] < profit_threshold:
            return False, f"Profit prob {sim['profit_prob']*100:.0f}% < {profit_threshold*100:.0f}% \u2014 STOP"
        if sim["expected_return"] < max_expected_loss:
            return False, f"Expected return {sim['expected_return']:.0f}% \u2014 STOP"

        return True, f"OK (ruin {sim['ruin_prob']*100:.0f}%, profit {sim['profit_prob']*100:.0f}%)"
