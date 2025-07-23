import requests
import math
import csv
import json

# -------------------------
# Glicko-2 with Margin-of-Victory
# -------------------------
class Glicko2Player:
    def __init__(self, rating=1500, rd=350, sigma=0.06):
        self.rating = rating
        self.rd = rd
        self.sigma = sigma

    def __str__(self):
        return f"R={self.rating:.1f}, RD={self.rd:.1f}, σ={self.sigma:.3f}"


class Glicko2System:
    TAU = 0.5
    BASE_RATING = 1500
    BASE_RD = 350
    BASE_SIGMA = 0.06
    q = math.log(10) / 400

    def __init__(self):
        self.players = {}

    def init_team(self, team, base_rating=1500):
        if team not in self.players:
            self.players[team] = Glicko2Player(rating=base_rating)

    def _g(self, phi):
        return 1 / math.sqrt(1 + (3 * self.q**2 * phi**2) / (math.pi**2))

    def _E(self, mu, mu_j, phi_j):
        return 1 / (1 + math.exp(-self._g(phi_j) * (mu - mu_j)))

    def _mov_multiplier(self, margin, rating_diff):
        """Margin-of-victory factor."""
        return math.log(margin + 1) * (2.2 / (rating_diff * 0.001 + 2.2))

    def update_player(self, player, results):
        if not results:
            return

        mu = (player.rating - self.BASE_RATING) / 173.7178
        phi = player.rd / 173.7178

        v_inv = 0
        delta_sum = 0
        for opp, score, margin in results:
            mu_j = (opp.rating - self.BASE_RATING) / 173.7178
            phi_j = opp.rd / 173.7178
            E = self._E(mu, mu_j, phi_j)
            g = self._g(phi_j)
            rating_diff = abs(player.rating - opp.rating)
            mov_factor = self._mov_multiplier(margin, rating_diff)

            v_inv += (g**2) * E * (1 - E)
            delta_sum += mov_factor * g * (score - E)

        v = 1 / v_inv
        phi_star = math.sqrt(phi**2 + player.sigma**2)
        phi_new = 1 / math.sqrt((1 / (phi_star**2)) + (1 / v))
        mu_new = mu + phi_new**2 * delta_sum

        player.rating = self.BASE_RATING + 173.7178 * mu_new
        player.rd = 173.7178 * phi_new

    def update_match(self, team_a, team_b, score_a, score_b):
        margin = abs(score_a - score_b)
        if score_a > score_b:
            result_a, result_b = 1, 0
        elif score_a < score_b:
            result_a, result_b = 0, 1
        else:
            result_a = result_b = 0.5
            margin = 1

        self.update_player(self.players[team_a], [(self.players[team_b], result_a, margin)])
        self.update_player(self.players[team_b], [(self.players[team_a], result_b, margin)])

    def get_ratings(self):
        return {team: p.rating for team, p in self.players.items()}


# -------------------------
# CFBD Data Fetch
# -------------------------
def fetch_games(api_key, season, week):
    url = f"https://api.collegefootballdata.com/games?year={season}&week={week}"
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code == 404:  # No games for that week
        return []
    resp.raise_for_status()
    return resp.json()

def fetch_conferences(api_key):
    url = "https://api.collegefootballdata.com/teams/fbs"
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    teams = resp.json()
    return {team['school']: team['conference'] for team in teams}


# -------------------------
# Export Functions
# -------------------------
def export_weekly_rankings_csv(weekly_rankings, filepath="weekly_rankings.csv"):
    with open(filepath, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Week", "Rank", "Team", "Rating"])
        for week, rankings in weekly_rankings.items():
            for rank, (team, rating) in enumerate(rankings, start=1):
                writer.writerow([week, rank, team, f"{rating:.1f}"])
    print(f"Weekly rankings exported to {filepath}")

def export_weekly_rankings_json(weekly_rankings, filepath="weekly_rankings.json"):
    json_data = {
        week: [{"rank": i + 1, "team": team, "rating": round(rating, 1)}
               for i, (team, rating) in enumerate(rankings)]
        for week, rankings in weekly_rankings.items()
    }
    with open(filepath, "w") as jsonfile:
        json.dump(json_data, jsonfile, indent=2)
    print(f"Weekly rankings exported to {filepath}")


# -------------------------
# Historical Warm-up
# -------------------------
def run_historical(api_key, start_year, end_year, base_elos):
    """Run Glicko-2 updates from start_year to end_year to warm up ratings."""
    conf_map = fetch_conferences(api_key)
    system = Glicko2System()

    # Initialize all FBS teams with base ratings
    for team, conf in conf_map.items():
        system.init_team(team, base_elos.get(conf, 1500))

    for year in range(start_year, end_year + 1):
        print(f"Processing season {year}...")
        for week in range(1, 16):  # Typical weeks in a season
            games = fetch_games(api_key, year, week)
            if not games:
                continue

            for gm in games:
                for side in ['home', 'away']:
                    team = gm[f"{side}Team"]
                    conf = conf_map.get(team, None)
                    system.init_team(team, base_elos.get(conf, 1500))

            for gm in games:
                tA = gm['homeTeam']
                tB = gm['awayTeam']
                sA = gm.get('homePoints', 0) or 0
                sB = gm.get('awayPoints', 0) or 0
                system.update_match(tA, tB, sA, sB)
    return system


# -------------------------
# Current Season Rankings
# -------------------------
def run_current_season(api_key, season, max_week, system, base_elos):
    conf_map = fetch_conferences(api_key)
    weekly_rankings = {}

    for week in range(1, max_week + 1):
        games = fetch_games(api_key, season, week)
        if not games:
            continue

        for gm in games:
            for side in ['home', 'away']:
                team = gm[f"{side}Team"]
                conf = conf_map.get(team, None)
                system.init_team(team, base_elos.get(conf, 1500))

        for gm in games:
            tA = gm['homeTeam']
            tB = gm['awayTeam']
            sA = gm.get('homePoints', 0) or 0
            sB = gm.get('awayPoints', 0) or 0
            system.update_match(tA, tB, sA, sB)

        weekly_rankings[week] = sorted(
            system.get_ratings().items(),
            key=lambda x: x[1],
            reverse=True
        )

    return weekly_rankings


# -------------------------
# Main
# -------------------------
if __name__ == "__main__":
    API_KEY = "NzqjVKK0tMab5dMe8wXBGhql5o+jefFhsyjTA37Ad6QNrwaNOyMnqZYTy4VaF5go"  # Replace with your CFBD API key
    BASE_ELOS = {
        'SEC': 1550,
        'Big Ten': 1530,
        'ACC': 1500,
        'Big 12': 1500,
        'Pac-12': 1500,
        'AAC': 1475,
        'Sun Belt': 1450,
        'Mountain West': 1450,
        'MAC': 1435,
        'Conference USA': 1435,
        'FBS Independents': 1450
    }

    # 1. Warm up ratings using historical data (2015–2024)
    glicko_system = run_historical(API_KEY, 2015, 2024, BASE_ELOS)

    # 2. Run rankings for 2025
    season = 2025
    max_week = 7  # Adjust as needed
    weekly = run_current_season(API_KEY, season, max_week, glicko_system, BASE_ELOS)

    # Print top 10 per week
    for w, rankings in weekly.items():
        print(f"\nWeek {w} Top 10 (Glicko-2 with MoV)")
        for i, (team, rating) in enumerate(rankings[:10], start=1):
            print(f"{i}. {team}: {rating:.1f}")

    # Export rankings
    export_weekly_rankings_csv(weekly, "weekly_rankings_2025.csv")
    export_weekly_rankings_json(weekly, "weekly_rankings_2025.json")
