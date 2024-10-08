import requests
import os
import time
import firebase_admin
from firebase_admin import credentials, firestore ,auth
from flask import Flask, request, jsonify
from flask_cors import CORS  
import logging
import assemblyai as aai
import google.generativeai as genai
from collections import defaultdict
from datetime import datetime
import dropbox
import tempfile 
import json

app = Flask(__name__)

CORS(app)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Firebase Admin SDK
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Third-party API URL and headers
API_URL = "https://api.meetingbaas.com/bots"
API_HEADERS = {
    "Content-Type": "application/json",
    "x-spoke-api-key": '9f82706f97e3a9af0384e61fff1a7b4f1babb37861c13a9507a3bbb6970de69b',
}
genai_api_key = "AIzaSyBA9pugaBbwTh39NGqhhmrYAs8cfU0Uh5k"
genai.configure(api_key=genai_api_key)
model = genai.GenerativeModel('gemini-1.5-flash')
aai.settings.api_key = "37b81fbd27f54a3a83c9e64dd1880ddc"

# Initialize Dropbox client with your API key
with open('serviceAccountKey.json', 'r') as json_file:
    service_account_data = json.load(json_file)

# Extract the Dropbox Access Token
DROPBOX_ACCESS_TOKEN = service_account_data["DROPBOX_ACCESS_TOKEN"]

# Initialize Dropbox object
dbx = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)

# Function to upload file to Dropbox
def upload_to_dropbox(file, file_name):
    print(f"Uploading {file_name} to Dropbox...")
    try:
        with open(file, 'rb') as f:
            dbx.files_upload(f.read(), f"/{file_name}")
            print(f"File {file_name} uploaded successfully.")
        return f"/{file_name}"
    except Exception as e:
        print(f"Failed to upload {file_name} to Dropbox: {str(e)}")
        return None

# @app.route('/signup', methods=['POST'])
# def signup():
#     data = request.json
#     email = data.get('email')
#     password = data.get('password')
#     name = data.get('name')
    
#     if not email or not password:
#         return jsonify({'error': 'Email and password are required'}), 400

#     try:
#         # Create user in Firebase Auth
#         user = auth.create_user(email=email, password=password)

#         # Store user details in Firestore
#         user_data = {
#             'uid': user.uid,
#             'email': email,
#             'name': name,
#             'created_at': firestore.SERVER_TIMESTAMP,
#         }
#         db.collection('users').document(user.uid).set(user_data)

#         return jsonify({'message': 'User created successfully', 'uid': user.uid}), 201
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500


# @app.route('/login', methods=['POST'])
# def login():
#     data = request.json
#     email = data.get('email')
#     password = data.get('password')

#     if not email or not password:
#         return jsonify({'error': 'Email and password are required'}), 400

#     try:
#         # Verify user's credentials (we use Firebase Auth REST API to simulate login)
#         auth_response = requests.post(f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={firebase_auth_key}', json={
#             'email': email,
#             'password': password,
#             'returnSecureToken': True
#         })
#         auth_data = auth_response.json()

#         if auth_response.status_code == 200:
#             return jsonify({'token': auth_data['idToken'], 'uid': auth_data['localId']}), 200
#         else:
#             return jsonify({'error': auth_data.get('error', {}).get('message', 'Login failed')}), 400
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500
  # Change to a valid directory on your system

@app.route('/verify-token', methods=['POST'])
def verify_token():
    try:
        # Get the ID token from the request body
        id_token = request.json.get('idToken')

        if not id_token:
            return jsonify({"message": "Missing token"}), 400

        # Verify the token using Firebase Admin SDK
        decoded_token = auth.verify_id_token(id_token)

        # If token is valid, return a success response
        uid = decoded_token['uid']
        return jsonify({"message": "Token is valid", "uid": uid}), 200

    except Exception as e:
        # Return an error response if verification fails
        return jsonify({"message": "Invalid or expired token", "error": str(e)}), 401
    
# Function to generate prompt based on the meeting type
def generate_prompt(meeting_type, transcript):
    print(f"Generating prompt for meeting type: {meeting_type}")
    switch = {
        "interview": f"Summarize the following interview transcript:\n{transcript}",
        "meeting": f"Summarize the following meeting transcript:\n{transcript}",
        "discussion": f"Summarize the following discussion transcript:\n{transcript}"
    }
    prompt = switch.get(meeting_type, f"Summarize the following transcript:\n{transcript}")
    print(f"Generated prompt: {prompt}")  # Print the generated prompt
    return prompt

def transcribe_audio(file_path):
    print(f"Starting transcription for: {file_path}")
    transcriber = aai.Transcriber()
    config = aai.TranscriptionConfig(speaker_labels=True)

    # Start transcription
    print("Starting transcription process...")
    transcript = transcriber.transcribe(file_path, config=config)
    print(f"Transcription started with ID: {transcript.id}")

    # Poll for the transcription status
    while transcript.status != aai.TranscriptStatus.completed and transcript.status != aai.TranscriptStatus.error:
        print(f"Polling for status, current status: {transcript.status}")
        time.sleep(5)
        transcript = transcriber.get_transcript(transcript.id)

    # Check for errors
    if transcript.status == aai.TranscriptStatus.error:
        print(f"Error in transcription: {transcript.error}")
        return {"error": transcript.error}, 500

    # Prepare the transcription result with speaker labels
    print("Transcription completed, preparing result...")
    result = []
    for utterance in transcript.utterances:
        result.append(f"Speaker {utterance.speaker}: {utterance.text}")
    
    print("Transcription result prepared.")
    return {"transcription": result}, 200

def summarize_transcript(transcript, meeting_type):
    try:
        # Join the transcription list into a single string
        merged_statements = "\n".join(transcript)
        
        # Generate the prompt based on meeting type
        prompt = generate_prompt(meeting_type, merged_statements)
        
        # Call the Google Gemini API to get the summary
        print("Calling Google Gemini API for summarization...")
        response = model.generate_content(prompt)
        summary = response.text.strip()
        print("Summary generated successfully.")
        return summary
    except AttributeError as e:
        print(f"AttributeError: {str(e)}")
        return f"Error: {str(e)}"
    except Exception as e:
        print(f"An unexpected error occurred: {str(e)}")
        return f"An unexpected error occurred: {str(e)}"


@app.route('/transcribe', methods=['POST'])
def transcribe():
    try:
        print("Checking if the request contains a file...")
        if 'file' not in request.files:
            print("No file part in the request.")
            return jsonify({"error": "No file part"}), 400

        file = request.files['file']
        if file.filename == '' or not file.filename.endswith('.mp3'):
            print(f"Invalid file type: {file.filename}")
            return jsonify({"error": "File must be an MP3"}), 400

        # Get user ID from the request
        user_id = request.form.get('user_id')
        if not user_id:
            return jsonify({"error": "user_id parameter is required"}), 400

        # Save the file temporarily in a cross-platform way
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as temp_file:
            temp_file_path = temp_file.name  # Get the name of the temporary file
            file.save(temp_file_path)
            print(f"File saved temporarily to {temp_file_path}")

        # Upload the file to Dropbox
        dropbox_file_path = upload_to_dropbox(temp_file_path, file.filename)

        if dropbox_file_path is None:
            return jsonify({"error": "Failed to upload file to Dropbox"}), 500

        # Transcribe the audio file
        print("Transcribing the file...")
        transcription_response, status_code = transcribe_audio(temp_file_path)

        if status_code != 200:
            return transcription_response, status_code

        # Generate the summary using the transcription
        meeting_type = request.form.get('meeting_type', 'meeting')  # Default to 'meeting' if not provided
        print(f"Received meeting type: {meeting_type}")  # Print the received meeting type
        summary = summarize_transcript(transcription_response['transcription'], meeting_type)
        print(f"Summary generated: {summary}")

        # Save transcription, summary, Dropbox file path, and timestamp to the user's uploads collection
        db.collection('users').document(user_id).collection('uploads').add({
            'file_name': file.filename,
            'dropbox_path': dropbox_file_path,
            'transcription': transcription_response['transcription'],
            'summary': summary,
            'timestamp': firestore.SERVER_TIMESTAMP  # Store the current timestamp
        })
       
        # Clean up by removing the temp file
        print(f"Removing temporary file {temp_file_path} after processing.")
        os.remove(temp_file_path)

        return jsonify({"transcription": transcription_response['transcription'], "summary": summary}), 200

    except Exception as e:
        print(f"An error occurred: {str(e)}")
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    
@app.route('/start-meeting-bot', methods=['POST'])
def start_meeting_bot():
    data = request.json
    meeting_url = data.get('meeting_url')
    user_id = data.get('user_id')  # Get the user_id from the request

    if not meeting_url or not user_id:
        return jsonify({"error": "Meeting URL and user_id are required"}), 400

    config = {
        "meeting_url": meeting_url,
        "bot_name": "AveryMeet AI Bot",
        "recording_mode": "speaker_view",
        "bot_image": "https://media-exp1.licdn.com/dms/image/C510BAQFO9wB5bgkHXA/company-logo_200_200/0?e=2159024400&v=beta&t=R8f-gia_POtjTafDcfamQViVHjyy0GRJDGjLOyjCJ2w",
        "entry_message": "I am AveryMeets's AI Bot, I am here to record this exchange to facilitate note-taking. This process is 100% automated, secure and confidential, strictly respecting your privacy and European GDPR standards. If you prefer not to use the service, the bot can be removed from the meeting upon simple request.",
        "reserved": False,
        "speech_to_text": "Gladia",
    }

    try:
        response = requests.post(API_URL, json=config, headers=API_HEADERS)
        response_data = response.json()

        if response.status_code == 200:
            bot_id = response_data.get("bot_id")

            if bot_id:
                # Save bot data under the user's document
                user_ref = db.collection('users').document(user_id)
                bot_ref = user_ref.collection('bots').document(bot_id)
                bot_ref.set({
                    "bot_id": bot_id,
                    "meetingUrl": meeting_url,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                })

                return jsonify({"message": "Bot sent successfully", "bot_id": bot_id}), 200
            else:
                return jsonify({"error": "Bot ID not found in the response"}), 500
        else:
            return jsonify({"error": f"Failed to send bot: {response_data}"}), response.status_code

    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500



    
@app.route('/meetings', methods=['GET'])
def get_user_meetings():
    try:
        user_id = request.args.get('user_id')  # Get the user_id from the request

        # Validate that user_id is provided
        if not user_id:
            return jsonify({'error': 'user_id is required'}), 400

        # Reference to the user's document in Firestore
        user_ref = db.collection('users').document(user_id)

        # Check if the user document exists
        user_doc = user_ref.get()
        if not user_doc.exists:
            return jsonify({'error': 'User does not exist!'}), 404

        # Reference to the 'bots' collection under the user's document
        meetings_ref = user_ref.collection('bots')
        meetings = []

        # Fetch all meeting summaries for the user
        docs = meetings_ref.stream()

        for doc in docs:
            meeting_data = doc.to_dict()
            meeting_data['id'] = doc.id
            meetings.append(meeting_data)

        logger.info("Meetings data retrieved successfully")
        return jsonify(meetings), 200

    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/meeting_data', methods=['GET'])
def get_meeting_data():
    bot_id = request.args.get('bot_id')

    if not bot_id:
        logger.error("bot_id parameter is required")
        return jsonify({'error': 'bot_id parameter is required'}), 400

    # Update the reference to get the correct bot document under the user's collection
    user_id = request.args.get('user_id')  # Get user_id as well to fetch the correct bot
    if not user_id:
        return jsonify({'error': 'user_id parameter is required'}), 400

    bot_doc_ref = db.collection('users').document(user_id).collection('bots').document(bot_id)
    bot_doc = bot_doc_ref.get()

    if not bot_doc.exists:
        logger.error("No such bot document!")
        return jsonify({'error': 'No such bot document!'}), 404

    bot_data = bot_doc.to_dict()
    meeting_data = {}
    meeting_datas = []

    # Check if the 'meeting_summary' subcollection exists and has documents
    meetings_ref = bot_doc_ref.collection('meeting_summary')
    meetings_docs = meetings_ref.stream()
    meetings_list = [doc.to_dict() for doc in meetings_docs]

    if meetings_list:
        # Meetings data found in Firestore
        logger.info(f"Meetings data found for bot_id {bot_id} in Firestore")
        return jsonify({'bot_data': bot_data, 'meeting_summary': meetings_list}), 200

    # If no data in Firestore, call the third-party API
    url = "https://api.meetingbaas.com/bots/meeting_data"
    params = {'bot_id': bot_id}
    headers = {
        "Content-Type": "application/json",
        "x-spoke-api-key": API_HEADERS["x-spoke-api-key"]
    }

    try:
        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 200:
            meeting_data = response.json()
            meeting_datas = response.json()
            print(meeting_datas)

            # Extract necessary data
            mp4_url = meeting_datas['assets'][0]['mp4_s3_path']
            print(mp4_url)
            attendees = meeting_datas['attendees']
            for attendee in attendees:
                print(f"Attendee: {attendee['name']}")

            # Extract transcription and summary
            def extract_speaker_statements(meeting_data):
                editors = meeting_data.get('editors', [])
                speaker_transcripts = defaultdict(lambda: defaultdict(list))
                for editor in editors:
                    video = editor.get('video', {})
                    transcripts = video.get('transcripts', [])
                    for transcript in transcripts:
                        speaker = transcript.get('speaker')
                        words = transcript.get('words', [])
                        if not words:
                            continue
                        start_time = words[0].get('start_time', 0.0)
                        text = ' '.join(word.get('text') for word in words if word.get('text')).strip()
                        speaker_transcripts[speaker][start_time].append(text)
                speaker_statements = []
                for speaker, timestamps in speaker_transcripts.items():
                    for start_time, texts in sorted(timestamps.items()):
                        full_statement = ' '.join(texts).strip()
                        speaker_statements.append(f"from {start_time:.2f}s {speaker} : {full_statement}")
                return speaker_statements

            def merge_statements(statements):
                merged_statements = []
                speaker_lines = defaultdict(list)

                for statement in statements:
                    parts = statement.split(' : ', 1)
                    if len(parts) < 2:
                        continue  # Skip malformed statements
                    timestamp_and_speaker = parts[0].split(' ', 2)
                    if len(timestamp_and_speaker) < 3:
                        continue  # Skip malformed timestamp and speaker part
                    timestamp = timestamp_and_speaker[1]  # Extract timestamp
                    speaker = timestamp_and_speaker[2]    # Extract speaker name
                    text = parts[1].strip()               # The actual text spoken
                    speaker_lines[(timestamp, speaker)].append(text)

                for (timestamp, speaker), texts in sorted(speaker_lines.items()):
                    full_statement = ' '.join(texts).strip()
                    merged_statements.append(f"{speaker} at {timestamp}s :- {full_statement}")

                return merged_statements

            speaker_statements = extract_speaker_statements(meeting_data)
            merged_statements = merge_statements(speaker_statements)

            # Summarize the transcript
            def summarize_transcript(statement):
                try:
                    transcript = "\n".join(statement)
                    prompt = f"Summarize the following meeting transcript:\n{transcript}"
                    response = model.generate_content(prompt)
                    summary = response.text.strip()
                    return f"{summary}"
                except AttributeError as e:
                    return f"Error: {str(e)}"
                except Exception as e:
                    return f"An unexpected error occurred: {str(e)}"

            summary = summarize_transcript(merged_statements)
            print(summary)

            # Prepare the meeting_summary object with attendees, transcription, summary, and mp4_url
            meeting_summary = {
                'attendees': attendees,
                'transcription': merged_statements,
                'summary': summary,
                'mp4_url': mp4_url
            }
            # Extract necessary data and process it as you did before...

            # Store the summary data in Firestore
            meeting_summary_firebase = {
                'attendees': attendees,
                'transcription': merged_statements,
                'summary': summary,
                'mp4_url': mp4_url,
                'timestamp': firestore.SERVER_TIMESTAMP,
            }
            bot_doc_ref.collection('meeting_summary').add(meeting_summary_firebase)  # Save to Firestore

            return jsonify({'bot_data': bot_data, 'meeting_summary': meeting_summary}), 200
        else:
            logger.error("Failed to retrieve meeting data from API")
            return jsonify({'error': 'Failed to retrieve meeting data from API'}), response.status_code

    except Exception as e:
        logger.error(f"An error occurred while retrieving meeting data: {str(e)}")
        return jsonify({'error': str(e)}), 500
    
    
# if __name__ == '__main__':
#     app.run(host='192.168.29.46', port=5000, debug=True)

@app.route('/last_meeting_summary', methods=['GET'])
def get_last_meeting_summary():
    user_id = request.args.get('user_id')  # Get user_id to fetch the correct bots
    if not user_id:
        logger.error("user_id parameter is required")
        return jsonify({'error': 'user_id parameter is required'}), 400

    # Fetch all bots for the user
    bots_ref = db.collection('users').document(user_id).collection('bots')
    bots_docs = bots_ref.stream()

    latest_meeting_summary = None
    latest_timestamp = 0

    # Iterate through each bot to find the latest meeting summary
    for bot_doc in bots_docs:
        meetings_ref = bot_doc.reference.collection('meeting_summary')
        meetings_docs = meetings_ref.stream()
        meetings_list = [doc.to_dict() for doc in meetings_docs]

        if meetings_list:
            # Check for the latest meeting summary within the meetings_list
            for meeting in meetings_list:
                # Convert timestamp to a comparable format
                meeting_timestamp = meeting.get('timestamp')

                if isinstance(meeting_timestamp, datetime):
                    meeting_timestamp = meeting_timestamp.timestamp()  # Convert to Unix timestamp

                if meeting_timestamp > latest_timestamp:
                    latest_timestamp = meeting_timestamp
                    latest_meeting_summary = meeting

    if latest_meeting_summary:
        logger.info("Latest meeting summary found")
        return jsonify({'meeting_summary' : latest_meeting_summary}), 200
    else:
        logger.warning("No meeting summaries found for the user")
        return jsonify({'error': 'No meeting summaries found for the user'}), 404
    

@app.route('/uploads', methods=['GET'])
def get_user_uploads():
    try:
        user_id = request.args.get('user_id')  # Get the user_id from the request

        # Validate that user_id is provided
        if not user_id:
            return jsonify({'error': 'user_id is required'}), 400

        # Reference to the user's document in Firestore
        user_ref = db.collection('users').document(user_id)

        # Check if the user document exists
        user_doc = user_ref.get()
        if not user_doc.exists:
            return jsonify({'error': 'User does not exist!'}), 404

        # Reference to the 'uploads' collection under the user's document
        uploads_ref = user_ref.collection('uploads')
        uploads = []

        # Fetch all uploads for the user
        docs = uploads_ref.stream()

        for doc in docs:
            upload_data = doc.to_dict()
            upload_data['id'] = doc.id
            uploads.append(upload_data)

        logger.info("Uploads data retrieved successfully")
        return jsonify(uploads), 200

    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/delete_upload', methods=['DELETE'])
def delete_upload():
    try:
        user_id = request.args.get('user_id')
        meeting_id = request.args.get('meeting_id')

        # Validate that both user_id and meeting_id are provided
        if not user_id or not meeting_id:
            return jsonify({'error': 'user_id and meeting_id are required'}), 400

        # Reference to the user's document in Firestore
        user_ref = db.collection('users').document(user_id)

        # Reference to the 'uploads' collection under the user's document
        uploads_ref = user_ref.collection('uploads').document(meeting_id)

        # Check if the meeting document exists
        if not uploads_ref.get().exists:
            return jsonify({'error': 'Meeting does not exist!'}), 404

        # Delete the meeting
        uploads_ref.delete()

        logger.info(f"Meeting with ID {meeting_id} deleted successfully for user {user_id}")
        return jsonify({'message': 'Meeting deleted successfully!'}), 200

    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)