import os, json, pickle, calendar, requests
from datetime import datetime, timedelta
from flask import Flask, redirect, session, url_for, render_template, request
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

# Permitir HTTP (solo local)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = Flask(__name__)
app.secret_key = 'supersecreto'

TOKEN_FILE = 'credentials/token.pkl'

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly"
]

def get_credentials_config():
    if 'GOOGLE_CREDENTIALS' in os.environ:
        return json.loads(os.environ['GOOGLE_CREDENTIALS'])
    else:
        with open('credentials.json') as f:
            return json.load(f)

def get_redirect_uri():
    if os.environ.get("RENDER", False):
        return "https://mi-mapa.onrender.com/oauth2callback"
    return "http://localhost:5000/oauth2callback"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login')
def login():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)

    cred_conf = get_credentials_config()
    flow = Flow.from_client_config(
        cred_conf,
        scopes=SCOPES,
        redirect_uri=get_redirect_uri()
    )

    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )

    session['state'] = state
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    code = request.args.get('code')
    cred_web = get_credentials_config()['web']

    token_resp = requests.post(cred_web['token_uri'], data={
        'code': code,
        'client_id': cred_web['client_id'],
        'client_secret': cred_web['client_secret'],
        'redirect_uri': get_redirect_uri(),
        'grant_type': 'authorization_code'
    })
    token_resp.raise_for_status()
    token_json = token_resp.json()

    creds = Credentials(
        token_json['access_token'],
        refresh_token=token_json.get('refresh_token'),
        token_uri=cred_web['token_uri'],
        client_id=cred_web['client_id'],
        client_secret=cred_web['client_secret'],
        scopes=SCOPES
    )

    os.makedirs('credentials', exist_ok=True)
    with open(TOKEN_FILE, 'wb') as tok:
        pickle.dump(creds, tok)

    session['email'] = build('oauth2', 'v2', credentials=creds).userinfo().get().execute()['email']
    return redirect(url_for('cuentas'))

@app.route('/cuentas')
def cuentas():
    creds = load_credentials()
    if not creds:
        return redirect(url_for('login'))
    return render_template('cuentas.html', correo=session.get('email'))

@app.route('/correos')
def correos():
    creds = load_credentials()
    if not creds:
        return redirect(url_for('login'))
    svc = build('gmail', 'v1', credentials=creds)
    msgs = svc.users().messages().list(userId='me', maxResults=5).execute().get('messages', [])
    correos = []
    for m in msgs:
        md = svc.users().messages().get(userId='me', id=m['id'], format='full').execute()
        hdr = md['payload']['headers']
        body = ''
        parts = md['payload'].get('parts', [])
        if parts:
            for part in parts:
                if part.get('mimeType') == 'text/plain' and 'data' in part.get('body', {}):
                    import base64
                    body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                    break
        else:
            body = md.get('snippet', '')

        correos.append({
            'from': next((h['value'] for h in hdr if h['name'] == 'From'), ''),
            'subject': next((h['value'] for h in hdr if h['name'] == 'Subject'), ''),
            'snippet': body
        })
    return render_template('correos.html', correos=correos)

@app.route('/classroom')
def classroom():
    creds = load_credentials()
    if not creds:
        return redirect(url_for('login'))

    svc = build('classroom', 'v1', credentials=creds)
    courses = svc.courses().list().execute().get('courses', [])
    publicaciones_por_curso = {}

    for c in courses:
        cid = c['id']
        cname = c.get('name', '[Sin nombre]')
        publicaciones = []

        ann = svc.courses().announcements().list(courseId=cid).execute().get('announcements', [])
        for a in ann:
            publicaciones.append({
                'titulo': a.get('text', '[Sin texto]'),
                'tipo': 'Anuncio',
                'fecha': a.get('updateTime')
            })

        cw = svc.courses().courseWork().list(courseId=cid).execute().get('courseWork', [])
        for t in cw:
            publicaciones.append({
                'titulo': t.get('title', '[Sin título]'),
                'tipo': 'Tarea',
                'fecha': t.get('updateTime')
            })

        mats = svc.courses().courseWorkMaterials().list(courseId=cid).execute().get('courseWorkMaterial', [])
        for m in mats:
            publicaciones.append({
                'titulo': m.get('title', '[Sin título]'),
                'tipo': 'Material',
                'fecha': m.get('updateTime')
            })

        publicaciones.sort(key=lambda x: x.get('fecha', ''), reverse=True)
        publicaciones_por_curso[cname] = publicaciones[:5]

    return render_template('classroom.html', publicaciones=publicaciones_por_curso)

@app.route('/calendario', methods=['GET', 'POST'])
def calendario():
    creds = load_credentials()
    if not creds:
        return redirect(url_for('login'))
    svc = build('calendar', 'v3', credentials=creds)
    if request.method == 'POST':
        t = request.form['titulo']
        f = request.form['fecha']
        h = request.form['hora']
        ev = {
            'summary': t,
            'start': {'dateTime': f + 'T' + h + ':00', 'timeZone': 'America/Lima'},
            'end': {'dateTime': f + 'T' + f"{int(h[:2]) + 1:02d}:{h[3:]}:00", 'timeZone': 'America/Lima'}
        }
        svc.events().insert(calendarId='primary', body=ev).execute()

    today = datetime.now()
    year, month = today.year, today.month
    cal = calendar.monthcalendar(year, month)
    start = datetime(year, month, 1).isoformat() + 'Z'
    end = (datetime(year, month, calendar.monthrange(year, month)[1]) + timedelta(days=1)).isoformat() + 'Z'
    evs = svc.events().list(calendarId='primary', timeMin=start, timeMax=end,
                             singleEvents=True, orderBy='startTime').execute().get('items', [])
    ev_by = {}
    for e in evs:
        d = int(e['start'].get('dateTime', e['start'].get('date', '')).split('T')[0].split('-')[2])
        ev_by.setdefault(d, []).append(e.get('summary', ''))

    return render_template('calendario.html', cal=cal, year=year, month=month, eventos_por_dia=ev_by)

@app.route('/logout')
def logout():
    session.clear()
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    return redirect(url_for('index'))

def load_credentials():
    if os.path.exists(TOKEN_FILE):
        return pickle.load(open(TOKEN_FILE, 'rb'))
    return None

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, port=port, host='0.0.0.0')