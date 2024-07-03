import os
import json
from flask import Flask, request, jsonify, session, redirect, url_for
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime, timezone, timedelta
import re
import secrets
import string
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Initialize Firebase Admin SDK
firebase_cred_str = os.getenv('firebase')
firebase_cred = json.loads(firebase_cred_str)
cred = credentials.Certificate(firebase_cred)
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://project-zeus-98a8c-default-rtdb.firebaseio.com/'
})

# Initialize Flask application
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'zeus')  # Replace with a random secret key

# Load OAuth client configuration from environment variable
client_secrets_str = os.getenv('oauth')

if client_secrets_str:
    try:
        client_secrets = json.loads(client_secrets_str)
    except json.JSONDecodeError as e:
        print(f"Error decoding OAuth client secrets: {e}")
        client_secrets = None
else:
    print("OAuth client secrets not found")
    client_secrets = None

SCOPES = ['https://www.googleapis.com/auth/contacts']

# Function to extract user identifier
def get_user(query):
    if query.get('isGroup'):
        return query.get('groupParticipant', '').replace(' ', '')
    else:
        sender = query.get('sender', '')
        return ''.join(c for c in sender if c.isdigit())

# Function to generate referral code
def generate_referral_code():
    code_length = 5
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(code_length))

# Route to register a user
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    query = data.get('query')

    message = query.get('message', '')
    username_match = re.search(r"register:\s*(\w+)", message)
    if not username_match:
        return jsonify({"replies": [{"message": "❌ Invalid registration format"}]}), 400
    username = username_match.group(1)

    referrer_code_match = re.search(r"referral:\s*(\w+)", message)
    referrer_code = referrer_code_match.group(1) if referrer_code_match else ""
    referral_code = generate_referral_code()

    user_identifier = get_user(query)
    # if number save it 
    contact_status = save(user_identifier)
    contact_status_str = str(contact_status)
    print(f"this is the contact status {contact_status_str}")
    id = "Z" + user_identifier[:4]
    
    users_ref = db.reference('users').order_by_child('identifier').equal_to(id)
    user_snapshot = users_ref.get()
    if user_snapshot:
        return jsonify({"replies": [{"message": "❌ User already exists"}]}), 400

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    yes_date = yesterday.strftime('%Y-%m-%d')

    level = 0
    if referrer_code:
        ref = db.reference('users').order_by_child('referralCode').equal_to(referrer_code)
        referrer = ref.get()
        if not referrer:
            return jsonify({"replies": [{"message": "❌ Invalid referral code"}]}), 400
        level = 1

    user_data = {
        'identifier': id,
        'username': username,
        'referrerCode': referrer_code,
        'level': level,
        'lastCheckInDate': yes_date,
        'referralCount': 0,
        'referralCode': referral_code,
        'streak': 0,
        'bestStreak': 0
    }

    db.reference('users').push(user_data)

    info = (
        "User Card😎\n"
        f"Level: {user_data['level']}\n"
        f"Best Streak: {user_data['bestStreak']}\n"
        f"Referral Code: {user_data['referralCode']} (note it down)\n"
    )
    response_message = f"🎉 Welcome {user_data['username']}!\n Upgraded to level {user_data['level']}🔥\n"
    return jsonify({"replies": [{"message": response_message + info + str(contact_status)}]}), 200


# Route to retrieve user info
@app.route('/info', methods=['POST'])
def info():
    data = request.json
    query = data.get('query')

    user_identifier = get_user(query)
    users_ref = db.reference('users').order_by_child('identifier').equal_to(user_identifier)
    user_snapshot = users_ref.get()

    if not user_snapshot:
        return jsonify({"replies": [{"message": "Please register first"}]}), 400

    user_data = list(user_snapshot.values())[0]

    info_message = (
        "Info😎\n"
        f"Username: {user_data['username']}\n"
        f"Level: {user_data['level']}\n"
        f"Streak: {user_data['streak']}\n"
        f"Best Streak: {user_data['bestStreak']}\n"
        f"Referral Code: {user_data['referralCode']}\n"
        f"Referral Count: {user_data['referralCount']}\n"
    )

    return jsonify({"replies": [{"message": info_message}]}), 200

# Route to perform daily check-in
@app.route('/checkin', methods=['POST'])
def checkin():
    data = request.json
    query = data.get('query')

    user_identifier = get_user(query)
    users_ref = db.reference('users').order_by_child('identifier').equal_to(user_identifier)
    user_snapshot = users_ref.get()

    if not user_snapshot:
        return jsonify({"replies": [{"message": "Please register first"}]}), 400

    user_data = list(user_snapshot.values())[0]
    now = datetime.now(timezone.utc)
    today_date = now.strftime('%Y-%m-%d')
    yesterday = now - timedelta(days=1)
    yes_date = yesterday.strftime('%Y-%m-%d')

    if user_data['lastCheckInDate'] == today_date:
        return jsonify({"replies": [{"message": "✅ Check-in has been already done"}]}), 200
    elif user_data['lastCheckInDate'] != yes_date:
        user_data['level'] = 1
        user_data['streak'] = 1
        msg = f"🔴 You broke your streak. Starting from level 1"
    else:
        user_data['level'] += 1
        user_data['lastCheckInDate'] = today_date
        user_data['streak'] += 1
        if user_data['streak'] > user_data['bestStreak']:
            user_data['bestStreak'] = user_data['streak']
        msg = f"🎉 Reached level {user_data['level']}"

    db.reference('users').child(list(user_snapshot.keys())[0]).update(user_data)

    return jsonify({"replies": [{"message": msg}]}), 200

# Route to save a contact to Google Contacts
@app.route('/save', methods=['POST'])
def save(number):
    id = "Z" + number[:4]
    try:
        credentials = load_credentials()

        if not credentials or not credentials.valid:
            raise RuntimeError("credentials not valid") 
        
        # Create credentials object from the token
        service = build('people', 'v1', credentials=credentials)

        contact = {
            'names': [{'givenName': id}],
            'phoneNumbers': [{'value': number, 'type': 'mobile'}]
        }

        saved_contact = service.people().createContact(body=contact).execute()
        print(saved_contact)
        return saved_contact, 200

    except Exception as e:
        return {"status": "error", "message": str(e)}

# OAuth authorization route
@app.route('/authorize')
def authorize():
    flow = Flow.from_client_config(client_secrets, scopes=SCOPES)
    flow.redirect_uri = url_for('oauth2callback', _external=True)

    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )

    session['state'] = state
    return redirect(authorization_url)

# OAuth callback route
@app.route('/oauth2callback')
def oauth2callback():
    state = session['state']
    flow = Flow.from_client_config(client_secrets, scopes=SCOPES, state=state)
    flow.redirect_uri = url_for('oauth2callback', _external=True)

    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    save_credentials(credentials)
    
def save_credentials(credentials):
    db.reference('/oauth_credentials').set(credentials.to_json())

def load_credentials():
    credentials_ref = db.reference('/oauth_credentials')
    credentials_json = credentials_ref.get()
    
    if credentials_json:
        return Credentials.from_authorized_user_info(credentials_json)
    else:
        return None

# Main index route
@app.route('/')
def index():
    return '<pre>Nothing to see here.\nCheckout README.md to start.</pre>'

if __name__ == '__main__':
    app.run(port=5000)
