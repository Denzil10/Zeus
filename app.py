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
from google.auth.transport.requests import Request

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
        id = query.get('groupParticipant', '').replace(' ', '')
    else:
        id = query.get('sender', '').replace(' ','')
    
    if id.startswith('~'):
        unknown = True 
    # have to remove the + sign but ~ is okay
        
    # code for handling normal contacts
    return id

# Function to generate referral code
def generate_referral_code():
    code_length = 5
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(code_length))

# Route to register a user
@app.route('/register', methods=['POST'])
def register(data=None):
    if not data:
        data = request.json
    query = data.get('query')
    
    if query.get('isGroup'):
        return jsonify({"replies": [{"message": "Register message should be directly send to Zeus bot"}]}), 200
    
    message = query.get('message', '')
    username_match = re.search(r"register:\s*(\w+)", message)
    if not username_match:
        return jsonify({"replies": [{"message": "Invalid registration format. Please refer manual"}]}), 200
    username = username_match.group(1)

    referrer_code_match = re.search(r"referral:\s*(\w+)", message)
    referrer_code = referrer_code_match.group(1) if referrer_code_match else ""
    referral_code = generate_referral_code()

    user_identifier = get_user(query)
    
    # check if saved
    id = user_identifier
    notSaved = user_identifier.startswith('~') or user_identifier.startswith('+')
    if notSaved:
        number = ''.join([c for c in user_identifier if c.isdigit()])
        id = "Z" + number[2:7] #considering indian numbers
    
    level = 0
    upgrade_phrase = "Starting from"
    if referrer_code:
        ref = db.reference('users').order_by_child('referralCode').equal_to(referrer_code)
        referrer = ref.get()
        if not referrer:
            return jsonify({"replies": [{"message": "Invalid referral code"}]}), 200
        referrer_data = list(referrer.values())[0]
        referrer_data['referralCount'] +=1
        level = 1
        upgrade_phrase = "Upgraded to"

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    yes_date = yesterday.strftime('%Y-%m-%d')

    users_ref = db.reference('users').order_by_child('identifier').equal_to(id)
    user_snapshot = users_ref.get()
    if user_snapshot:
        return jsonify({"replies": [{"message": "User already exists"}]}), 200
    
    # save new number
    contact_status = save(user_identifier)
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
        "*User Card*😎\n"
        # f"Identifier {user_data['identifier']}\n"
        f"Best Streak: {user_data['bestStreak']}\n"
        f"Referral Code: {user_data['referralCode']}\n"
    )
    
    response_message = f"⚡Welcome {user_data['username']}!\n {upgrade_phrase} level {user_data['level']}⚡\n\n"
    return jsonify({"replies": [{"message": response_message + info}]}), 200

# Route to retrieve user info
@app.route('/info', methods=['POST'])
def info(data= None):
    if not data:
        data = request.json
    query = data.get('query')

    user_identifier = get_user(query)
    users_ref = db.reference('users').order_by_child('identifier').equal_to(user_identifier)
    user_snapshot = users_ref.get()

    if not query.get('isGroup'):
        return jsonify({"replies": [{"message": "Commands like info and checkin should be done on group"}]}), 200
    
    # either not saved or contact not registered 
    notSaved = user_identifier.startswith('~') or not user_snapshot
    if notSaved:
        return jsonify({"replies": [{"message": "1. Please register on my DM first\n2. If already done try after a minute\n3. Still having issues? message me directly"}]}), 200

    user_data = list(user_snapshot.values())[0]

    info_message = (
        "⚡ *Info* ⚡\n"
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
def checkin(data=None):
    if not data:
        data = request.json
    query = data.get('query')

    user_identifier = get_user(query)
    users_ref = db.reference('users').order_by_child('identifier').equal_to(user_identifier)
    user_snapshot = users_ref.get()
    
    if not query.get('isGroup'):
        return jsonify({"replies": [{"message": "Commands like info and checkin should be done on group"}]}), 200

    # either not saved or contact not registered 
    notSaved = user_identifier.startswith('~') or not user_snapshot
    if notSaved:
        return jsonify({"replies": [{"message": "Please register on DM first. If you have just done it wait for some time as onboarding can take upto 2 minutes.\nStill having issues? message \"help\" to the bot"}]}), 200
    
    user_data = list(user_snapshot.values())[0]
    now = datetime.now(timezone.utc)
    today_date = now.strftime('%Y-%m-%d')
    yesterday = now - timedelta(days=1)
    yes_date = yesterday.strftime('%Y-%m-%d')

    if user_data['lastCheckInDate'] == today_date:
        return jsonify({"replies": [{"message": "Next check-in will be tomorrow"}]}), 200
    elif user_data['lastCheckInDate'] != yes_date:
        msg = f"🐣Oops! {user_data['username']}'s streak was broken at lvl {user_data['level']}🐣\nStarting from lvl 1"
        user_data['level'] = 1
        user_data['streak'] = 1
        user_data['lastCheckInDate'] = today_date
    else:
        user_data['level'] += 1
        user_data['lastCheckInDate'] = today_date
        user_data['streak'] += 1
        if user_data['streak'] > user_data['bestStreak']:
            user_data['bestStreak'] = user_data['streak']
        msg = f"⚡{user_data['username']} reached level {user_data['level']}⚡"

    db.reference('users').child(list(user_snapshot.keys())[0]).update(user_data)

    return jsonify({"replies": [{"message": msg}]}), 200

# Save OAuth credentials to Firebase
def save_credentials(credentials):
    ref = db.reference('oauth_credentials')
    ref.set({
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret
    })

def load_credentials():
    ref = db.reference('oauth_credentials')
    stored_credentials = ref.get()
    if stored_credentials:
        credentials = Credentials(
            stored_credentials['token'],
            refresh_token=stored_credentials.get('refresh_token'),
            token_uri=stored_credentials['token_uri'],
            client_id=stored_credentials['client_id'],
            client_secret=stored_credentials['client_secret']
        )
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            save_credentials(credentials)
        return credentials
    else:
        raise RuntimeError("Credentials not found in Firebase Realtime Database")

# OAuth authorization route
@app.route('/authorize')
def authorize():
    flow = Flow.from_client_config(client_secrets, scopes=SCOPES)
    flow.redirect_uri = url_for('oauth2callback', _external=True)

    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )

    session['state'] = state
    return redirect(authorization_url)

# OAuth callback route
@app.route('/oauth2callback')
def oauth2callback():
    state = session.get('state')
    if not state or state != request.args.get('state'):
        return jsonify({"error": "State mismatch error"}), 400

    flow = Flow.from_client_config(client_secrets, scopes=SCOPES, state=state)
    flow.redirect_uri = url_for('oauth2callback', _external=True)

    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    # print(f"referesh {credentials.refresh_token}")
    save_credentials(credentials)

    return jsonify({"message": f"Authorization successful, credentials saved with refresh {credentials.refresh_token}"}), 200

# Route to save a contact to Google Contacts
@app.route('/save', methods=['POST'])
def save(number):
    number = ''.join([char for char in number if char.isdigit()])
    id = "Z" + number[2:7]

    try:
        credentials = load_credentials()
        if not credentials or not credentials.valid:
            raise RuntimeError("Credentials not valid")

        service = build('people', 'v1', credentials=credentials)

        contact = {
            'names': [{'givenName': id}],
            'phoneNumbers': [{'value': number, 'type': 'mobile'}]
        }

        saved_contact = service.people().createContact(body=contact).execute()
        return jsonify({'message': 'Contact saved successfully', 'contact': saved_contact}), 200

    except Exception as e:
        print(f"Error saving contact: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400
    
@app.route('/leaderboard', methods=['GET'])
def leaderboard():
    # Fetch all users from the database
    users_ref = db.reference('users')
    users_snapshot = users_ref.get()

    if not users_snapshot:
        return jsonify({"replies": [{"message": "No users found."}]}), 200

    # Create a list of user data dictionaries
    users_data = list(users_snapshot.values())

    # Sort the users by their streak in descending order
    sorted_users = sorted(users_data, key=lambda x: x['streak'], reverse=True)

    # Format the leaderboard message
    leaderboard_message = "🏆 *Leaderboard* 🏆\n"
    for i, user in enumerate(sorted_users[:10], 1):  # Limit to top 10 users
        leaderboard_message += (
            f"{i}. {user['username']} - Level: {user['level']}, Streak: {user['streak']}, "
            f"Best Streak: {user['bestStreak']}\n"
        )

    return jsonify({"replies": [{"message": leaderboard_message}]}), 200


@app.route('/any', methods=['POST'])
def route_message():
    data = request.json
    query = data.get('query')
    message = query.get('message')
    first_word = message.split()[0].lower() if message else ''

    if first_word == 'register:':
        return register(data)
    elif first_word == 'info':
        return info(data)
    elif first_word == 'checkin':
        return checkin(data)
    elif first_word == 'leaderboardroot':
        return leaderboard()
    else:
        return jsonify({"replies": [{"message": f"Invalid command, please refer manual\nYou can discuss on general group " }]}), 200


# Main index route
@app.route('/')
def index():
    return '<pre>Nothing to see here.\nCheckout README.md to start.</pre>'

if __name__ == '__main__':
    app.run(port=5000)