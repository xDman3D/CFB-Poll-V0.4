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
def fetch_all_games(api_key, season):
    """
    Fetch all games for a season in one API call.
    """
    url = f"https://api.collegefootballdata.com/games?year={season}"
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code == 404:
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
def run_historical(api_key, start_year, end_year, base_elos, conf_map, FBS_TEAMS):
    system = Glicko2System()

    # Initialize all FBS teams
    for team, conf in conf_map.items():
        system.init_team(team, base_elos.get(conf, 1500))

    for year in range(start_year, end_year + 1):
        print(f"Processing season {year}...")
        games = fetch_all_games(api_key, year)
        if not games:
            continue

        weeks = sorted({gm['week'] for gm in games if 'week' in gm})
        for week in weeks:
            week_games = [gm for gm in games if gm.get('week') == week]
            for gm in week_games:
                tA = gm.get('homeTeam') or gm.get('home_team')
                tB = gm.get('awayTeam') or gm.get('away_team')
                sA = gm.get('homePoints', 0) or 0
                sB = gm.get('awayPoints', 0) or 0

                is_A_fbs = tA in FBS_TEAMS
                is_B_fbs = tB in FBS_TEAMS

                if is_A_fbs and is_B_fbs:
                    system.update_match(tA, tB, sA, sB)
                elif is_A_fbs or is_B_fbs:
                    # FBS vs. FCS game
                    fbs_team = tA if is_A_fbs else tB
                    fbs_score = sA if is_A_fbs else sB
                    fcs_score = sB if is_A_fbs else sA

                    temp_fcs_player = Glicko2Player(rating=1400)
                    result = 1 if fbs_score > fcs_score else (0 if fbs_score < fcs_score else 0.5)
                    margin = abs(fbs_score - fcs_score) or 1
                    system.update_player(system.players[fbs_team], [(temp_fcs_player, result, margin)])

        print(f"Finished processing {year} with {len(system.players)} FBS teams rated.")

    return system


# -------------------------
# Current Season Rankings
# -------------------------
def run_current_season(api_key, season, max_week, system, base_elos, FBS_TEAMS):
    games = fetch_all_games(api_key, season)
    if not games:
        return {}

    weeks = sorted({gm['week'] for gm in games if 'week' in gm})
    weekly_rankings = {}

    for week in weeks:
        if week > max_week:
            break

        week_games = [gm for gm in games if gm.get('week') == week]
        for gm in week_games:
            tA = gm.get('homeTeam') or gm.get('home_team')
            tB = gm.get('awayTeam') or gm.get('away_team')
            sA = gm.get('homePoints', 0) or 0
            sB = gm.get('awayPoints', 0) or 0

            is_A_fbs = tA in FBS_TEAMS
            is_B_fbs = tB in FBS_TEAMS

            if is_A_fbs and is_B_fbs:
                system.update_match(tA, tB, sA, sB)
            elif is_A_fbs or is_B_fbs:
                fbs_team = tA if is_A_fbs else tB
                fbs_score = sA if is_A_fbs else sB
                fcs_score = sB if is_A_fbs else sA

                temp_fcs_player = Glicko2Player(rating=1400)
                result = 1 if fbs_score > fcs_score else (0 if fbs_score < fcs_score else 0.5)
                margin = abs(fbs_score - fcs_score) or 1
                system.update_player(system.players[fbs_team], [(temp_fcs_player, result, margin)])

        weekly_rankings[week] = sorted(
            [(team, rating) for team, rating in system.get_ratings().items() if team in FBS_TEAMS],
            key=lambda x: x[1],
            reverse=True
        )

    return weekly_rankings


# -------------------------
# Main
# -------------------------
if __name__ == "__main__":
    API_KEY = "YOUR_CFBD_API_KEY"  # Replace with your CFBD API key
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

    # Fetch conference map once
    conf_map = fetch_conferences(API_KEY)
    FBS_TEAMS = set(conf_map.keys())

    # 1. Warm up ratings using historical data (2015–2024)
    glicko_system = run_historical(API_KEY, 2015, 2024, BASE_ELOS, conf_map, FBS_TEAMS)

    # 2. Run rankings for 2025
    season = 2025
    max_week = 7  # Adjust as needed
    weekly = run_current_season(API_KEY, season, max_week, glicko_system, BASE_ELOS, FBS_TEAMS)

    # Print full rankings per week
    for w, rankings in weekly.items():
        print(f"\n=== Week {w} Full Rankings (Glicko-2 with MoV) ===")
        for i, (team, rating) in enumerate(rankings, start=1):
            print(f"{i}. {team}: {rating:.1f}")

    # Export rankings
    export_weekly_rankings_csv(weekly, "weekly_rankings_2025.csv")
    export_weekly_rankings_json(weekly, "weekly_rankings_2025.json")
