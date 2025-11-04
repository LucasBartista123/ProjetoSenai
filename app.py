import os
from flask import Flask, render_template, request, redirect, url_for
import requests
from dotenv import load_dotenv

load_dotenv() 

app = Flask(__name__)

STEAM_API_KEY = os.getenv("STEAM_API_KEY")
FACEIT_API_KEY = os.getenv("FACEIT_API_KEY") 

if not STEAM_API_KEY or not FACEIT_API_KEY:
    raise ValueError("Chaves de API (STEAM_API_KEY ou FACEIT_API_KEY) não encontradas. Verifique seu .env")

STEAM_API_BASE_URL = "http://api.steampowered.com"
RESOLVE_VANITY_URL = f"{STEAM_API_BASE_URL}/ISteamUser/ResolveVanityURL/v0001/"
GET_PLAYER_SUMMARIES = f"{STEAM_API_BASE_URL}/ISteamUser/GetPlayerSummaries/v0002/"
GET_USER_STATS = f"{STEAM_API_BASE_URL}/ISteamUserStats/GetUserStatsForGame/v0002/"
GET_OWNED_GAMES = f"{STEAM_API_BASE_URL}/IPlayerService/GetOwnedGames/v0001/"
CS2_APP_ID = 730

FACEIT_API_BASE_URL = "https://open.faceit.com/data/v4"

def get_steam_id64(input_query):
    identifier = input_query.strip("/").split("/")[-1]
    
    if identifier.isdigit() and len(identifier) == 17:
        return identifier 

    params = {'key': STEAM_API_KEY, 'vanityurl': identifier}
    try:
        response = requests.get(RESOLVE_VANITY_URL, params=params, timeout=5)
        data = response.json()
        
        if data['response']['success'] == 1:
            return data['response']['steamid']
        else:
            return None
    except Exception as e:
        print(f"Erro ao resolver URL: {e}")
        return None

def get_player_data(steam_id64):
    if not steam_id64:
        return {'error': 'ID da Steam não encontrado.'}
    
    player_data = {}
    try:
        params_summary = {'key': STEAM_API_KEY, 'steamids': steam_id64}
        resp_summary = requests.get(GET_PLAYER_SUMMARIES, params=params_summary, timeout=5).json()
        if not resp_summary['response']['players']:
             return {'error': 'Jogador não encontrado com este SteamID.'}
        player_data['profile'] = resp_summary['response']['players'][0]

        params_games = {
            'key': STEAM_API_KEY, 
            'steamid': steam_id64, 
            'format': 'json', 
            'include_played_free_games': 1 
        }
        resp_games = requests.get(GET_OWNED_GAMES, params=params_games, timeout=5).json()
        player_data['playtime_cs2_hours'] = 0
        if 'response' in resp_games and 'games' in resp_games['response']:
            for game in resp_games['response']['games']:
                if game['appid'] == CS2_APP_ID:
                    player_data['playtime_cs2_hours'] = round(game['playtime_forever'] / 60)
                    break
        
        params_stats = {'key': STEAM_API_KEY, 'steamid': steam_id64, 'appid': CS2_APP_ID}
        resp_stats = requests.get(GET_USER_STATS, params=params_stats, timeout=5).json()
        
        stats_dict = {}
        if 'playerstats' in resp_stats and 'stats' in resp_stats['playerstats']:
            for stat in resp_stats['playerstats']['stats']:
                stats_dict[stat['name']] = stat['value']
            player_data['stats'] = stats_dict
            player_data['profile_public'] = True
        else:
            player_data['profile_public'] = False
    
    except Exception as e:
        print(f"Erro ao buscar dados do jogador (Steam): {e}")
        return {'error': f'Erro ao buscar dados da Steam: {e}'}

    return player_data

def get_faceit_data(steam_id64):
    headers = {
        'Authorization': f'Bearer {FACEIT_API_KEY}',
        'accept': 'application/json'
    }
    faceit_data = {}

    try:
        url_search = f"{FACEIT_API_BASE_URL}/players?game=cs2&game_player_id={steam_id64}"
        resp_search = requests.get(url_search, headers=headers, timeout=5) 
        
        if resp_search.status_code != 200:
            return None
        
        player_info = resp_search.json()
        player_id = player_info.get('player_id') 
        
        if not player_id:
            return None
            
        faceit_data['profile'] = player_info

        url_stats = f"{FACEIT_API_BASE_URL}/players/{player_id}/stats/cs2"
        resp_stats = requests.get(url_stats, headers=headers, timeout=5)
        
        if resp_stats.status_code == 200:
            faceit_data['stats'] = resp_stats.json().get('lifetime', {})
        else:
            faceit_data['stats'] = {} 
            
        url_history = f"{FACEIT_API_BASE_URL}/players/{player_id}/history?game=cs2&offset=0&limit=10"
        resp_history = requests.get(url_history, headers=headers, timeout=5)
        
        if resp_history.status_code != 200:
            faceit_data['history'] = []
            return faceit_data

        match_list = resp_history.json().get('items', [])
        detailed_match_list = []

        for match in match_list:
            match_id = match.get('match_id')
            if not match_id:
                continue

            url_match_stats = f"{FACEIT_API_BASE_URL}/matches/{match_id}/stats"
            resp_match_stats = requests.get(url_match_stats, headers=headers, timeout=5)
            
            if resp_match_stats.status_code != 200:
                detailed_match_list.append(match) 
                continue

            stats_data = resp_match_stats.json()
            found_player_stats = False
            
            for team in stats_data.get('rounds', [{}])[0].get('teams', []):
                for player in team.get('players', []):
                    if player.get('player_id') == player_id:
                        
                        player_stats = player.get('player_stats', {})
                        round_stats = stats_data.get('rounds', [{}])[0].get('round_stats', {})
                        
                        match['stats'] = {
                            'result': 'Venceu' if player_stats.get('Result') == '1' else 'Perdeu',
                            'score': round_stats.get('Score', 'N/A'),
                            'map': round_stats.get('Map', 'N/A'),
                            'kills': player_stats.get('Kills', '0'),
                            'deaths': player_stats.get('Deaths', '0'),
                            'assists': player_stats.get('Assists', '0')
                        }
                        found_player_stats = True
                        break
                if found_player_stats:
                    break
            
            detailed_match_list.append(match)

        faceit_data['history'] = detailed_match_list

    except Exception as e:
        print(f"Erro ao buscar dados do jogador (FACEIT): {e}")
        return None 

    return faceit_data

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search')
def search():
    query = request.args.get('query')
    if not query:
        return redirect(url_for('index'))
    
    steam_id64 = get_steam_id64(query) 
    
    if steam_id64:
        return redirect(url_for('perfil_page', steam_id64=steam_id64))
    else:
        return redirect(url_for('index'))

@app.route('/perfil/<steam_id64>')
def perfil_page(steam_id64):
    data = get_player_data(steam_id64)
    if 'error' in data:
        return f"Erro: {data['error']}"
    
    data['faceit'] = get_faceit_data(steam_id64)
    
    return render_template('perfil.html', data=data)

if __name__ == '__main__':
    os.environ['FLASK_ENV'] = 'development'
    app.run(debug=True)