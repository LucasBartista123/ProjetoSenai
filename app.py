import os
import secrets
import re
from PIL import Image
from flask import Flask, render_template, request, redirect, url_for, g, session, flash
import requests
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, PasswordField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, ValidationError, Length, URL
from flask_bcrypt import Bcrypt
from datetime import datetime

# --- Carregar variáveis de ambiente ---
# Garante que o .env seja encontrado mesmo se rodar de outro diretório
from pathlib import Path
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(env_path)

app = Flask(__name__)

# --- Variáveis de ambiente obrigatórias ---
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
FACEIT_API_KEY = os.getenv("FACEIT_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")

# Verifica se todas as variáveis foram carregadas
missing_envs = [k for k, v in {
    "STEAM_API_KEY": STEAM_API_KEY,
    "FACEIT_API_KEY": FACEIT_API_KEY,
    "DATABASE_URL": DATABASE_URL,
    "SECRET_KEY": SECRET_KEY
}.items() if not v]

if missing_envs:
    raise ValueError(
        f"As seguintes variáveis de ambiente não foram encontradas: {', '.join(missing_envs)}. "
        f"Verifique seu arquivo .env ou as variáveis no painel da Square Cloud."
    )

# --- Configuração do Flask e banco ---
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Você precisa estar logado para ver esta página.'
login_manager.login_message_category = 'info'

# --- Modelos do banco ---
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    steam_id = db.Column(db.String(40), unique=True, nullable=True)
    google_id = db.Column(db.String(40), unique=True, nullable=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    avatar_url = db.Column(db.String(255), nullable=False, default='default.png')
    password_hash = db.Column(db.String(128), nullable=False)
    
    clips = db.relationship('Clip', backref='author', lazy=True)
    
    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'

class Clip(db.Model):
    __tablename__ = 'clips'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    video_url = db.Column(db.String(255), nullable=False)
    date_posted = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    def __repr__(self):
        return f'<Clip {self.title}>'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Formulários ---
class RegistrationForm(FlaskForm):
    username = StringField('Nome de Usuário', validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired()])
    password = PasswordField('Senha', validators=[DataRequired()])
    confirm_password = PasswordField('Confirmar Senha', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Registrar-se')

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('Este nome de usuário já está em uso.')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('Este email já está em uso.')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired()])
    password = PasswordField('Senha', validators=[DataRequired()])
    submit = SubmitField('Login')

class UpdateAccountForm(FlaskForm):
    username = StringField('Nome de Usuário', validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired()])
    picture = FileField('Atualizar Foto de Perfil', validators=[FileAllowed(['jpg', 'png'])])
    submit = SubmitField('Atualizar')

    def validate_username(self, username):
        if username.data != current_user.username:
            user = User.query.filter_by(username=username.data).first()
            if user:
                raise ValidationError('Este nome de usuário já está em uso.')

    def validate_email(self, email):
        if email.data != current_user.email:
            user = User.query.filter_by(email=email.data).first()
            if user:
                raise ValidationError('Este email já está em uso.')

class ClipForm(FlaskForm):
    title = StringField('Título do Clipe', validators=[DataRequired(), Length(min=2, max=100)])
    video_url = StringField('URL do Vídeo (Apenas YouTube)', validators=[DataRequired(), URL()])
    submit = SubmitField('Postar Clipe')

# --- Funções auxiliares ---
def save_picture(form_picture):
    random_hex = secrets.token_hex(8)
    _, f_ext = os.path.splitext(form_picture.filename)
    picture_fn = random_hex + f_ext
    picture_path = os.path.join(app.root_path, 'static/img/profile_pics', picture_fn)

    output_size = (125, 125)
    i = Image.open(form_picture)
    i.thumbnail(output_size)
    i.save(picture_path)

    return picture_fn

def get_embed_url(video_url):
    youtube_regex = re.compile(
        r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})'
    )
    match = youtube_regex.search(video_url)
    if match:
        return f"https://www.youtube.com/embed/{match.group(4)}"
    return None

@app.context_processor
def utility_processor():
    return dict(get_embed_url=get_embed_url)

# --- Constantes da Steam e Faceit ---
STEAM_API_BASE_URL = "http://api.steampowered.com"
RESOLVE_VANITY_URL = f"{STEAM_API_BASE_URL}/ISteamUser/ResolveVanityURL/v0001/"
GET_PLAYER_SUMMARIES = f"{STEAM_API_BASE_URL}/ISteamUser/GetPlayerSummaries/v0002/"
GET_USER_STATS = f"{STEAM_API_BASE_URL}/ISteamUserStats/GetUserStatsForGame/v0002/"
GET_OWNED_GAMES = f"{STEAM_API_BASE_URL}/IPlayerService/GetOwnedGames/v0001/"
CS2_APP_ID = 730

FACEIT_API_BASE_URL = "https://open.faceit.com/data/v4"

# --- Funções API Steam ---
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

        params_games = {'key': STEAM_API_KEY, 'steamid': steam_id64, 'format': 'json', 'include_played_free_games': 1}
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

# --- Função API Faceit ---
def get_faceit_data(steam_id64):
    headers = {'Authorization': f'Bearer {FACEIT_API_KEY}', 'accept': 'application/json'}
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

# --- Rotas Flask ---
@app.route('/')
def index():
    clips = Clip.query.order_by(Clip.date_posted.desc()).all()
    return render_template('index.html', clips=clips)

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

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Sua conta foi criada! Você já pode fazer login.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', title='Registrar-se', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=True)
            return redirect(url_for('index'))
        else:
            flash('Login sem sucesso. Verifique seu email e senha.', 'danger')
    return render_template('login.html', title='Login', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    form = UpdateAccountForm()
    if form.validate_on_submit():
        if form.picture.data:
            picture_file = save_picture(form.picture.data)
            current_user.avatar_url = picture_file
        current_user.username = form.username.data
        current_user.email = form.email.data
        db.session.commit()
        flash('Sua conta foi atualizada!', 'success')
        return redirect(url_for('account'))
    elif request.method == 'GET':
        form.username.data = current_user.username
        form.email.data = current_user.email
    image_file = url_for('static', filename='img/profile_pics/' + current_user.avatar_url)
    return render_template('account.html', title='Conta', image_file=image_file, form=form)

@app.route("/user/<string:username>")
def user_profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    image_file = url_for('static', filename='img/profile_pics/' + user.avatar_url)
    clips = Clip.query.filter_by(author=user).order_by(Clip.date_posted.desc()).all()
    return render_template('user.html', user=user, image_file=image_file, clips=clips)

@app.route("/postar-clipe", methods=['GET', 'POST'])
@login_required
def post_clip():
    form = ClipForm()
    if form.validate_on_submit():
        clip = Clip(title=form.title.data, video_url=form.video_url.data, author=current_user)
        db.session.add(clip)
        db.session.commit()
        flash('Seu clipe foi postado com sucesso!', 'success')
        return redirect(url_for('user_profile', username=current_user.username))
    return render_template('postar_clipe.html', title='Postar Clipe', form=form)

# --- Inicialização ---
if __name__ == '__main__':
    os.environ['FLASK_ENV'] = 'development'
    app.run(debug=True)
