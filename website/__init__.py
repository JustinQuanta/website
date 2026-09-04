from flask import Flask, current_app
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import os
from os import path, environ
from flask_login import LoginManager
from flask_mail import Mail
import pytz
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from .data_jobs import run_data_collection
from seed_tickers import seed_database
from flask_session import Session
import tempfile

db = SQLAlchemy()
DB_NAME = "database.db"
mail = Mail()
load_dotenv() # This is the key line

def format_datetime_to_utc_iso_string(dt):
    """
    Ensures a datetime is UTC and returns it as an ISO string with 'Z'.
    """
    if dt is None:
        return ""
    if not isinstance(dt, datetime):
        # Attempt to convert if it's a string that might be a date
        # This part might need more robust parsing if dt can be various string formats
        try:
            dt = datetime.fromisoformat(str(dt).replace('Z', '+00:00'))
        except:
            current_app.logger.warning(f"Could not parse '{str(dt)}' as datetime for UTC conversion.")
            return str(dt) # Fallback to string representation

    if dt.tzinfo is None: # If naive, assume it's UTC as per your DB storage intention
        dt_aware_utc = pytz.utc.localize(dt)
    else: # If aware, convert to UTC just to be sure
        dt_aware_utc = dt.astimezone(pytz.utc)

    return dt_aware_utc.isoformat().replace('+00:00', 'Z') # Ensure 'Z' for UTC

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config['SECRET_KEY'] = environ.get('SECRET_KEY')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    SESSION_FILE_DIR = tempfile.mkdtemp()
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['SESSION_FILE_DIR'] = SESSION_FILE_DIR
    app.config['SESSION_PERMANENT'] = True
    #app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
    
    # Ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass # Already exists
    
    # Flask-Mail Configuration (IMPORTANT: Use your actual email provider's details)
    app.config['MAIL_SERVER'] = environ.get('MAIL_SERVER')  # e.g., 'smtp.gmail.com' for Gmail
    app.config['MAIL_PORT'] = int(environ.get('MAIL_PORT', 587))                   # Standard port for TLS
    app.config['MAIL_USE_TLS'] = environ.get('MAIL_USE_TLS', 'True').lower() == 'true'              # Use TLS
    app.config['MAIL_USE_SSL'] = environ.get('MAIL_USE_SSL', 'False').lower() == 'true'             # Typically False if using TLS on port 587
    app.config['MAIL_USERNAME'] = environ.get('MAIL_USERNAME') # Your sending email address
    app.config['MAIL_PASSWORD'] = environ.get('MAIL_PASSWORD') # Your email password or an App Password (recommended for Gmail)
    
    # Reconstruct the MAIL_DEFAULT_SENDER tuple
    sender_name = environ.get('MAIL_DEFAULT_SENDER_NAME', 'Quantarize')
    sender_email = environ.get('MAIL_DEFAULT_SENDER_EMAIL', 'your-default-email@example.com') # Fallback if not set
    if environ.get('MAIL_USERNAME') and not sender_email: # If default sender email is not set, use MAIL_USERNAME
        sender_email = environ.get('MAIL_USERNAME')
    app.config['MAIL_DEFAULT_SENDER'] = (sender_name, sender_email)
    
    db.init_app(app)
    mail.init_app(app)
    Session(app)
    app.jinja_env.filters['to_utc_iso'] = format_datetime_to_utc_iso_string
    
    from .views import views
    from .auth import auth
    app.register_blueprint(views, url_prefix='/')
    app.register_blueprint(auth, url_prefix='/')

    from .models import User, Note # Make sure ValuationLog is also imported if you use it

    # --- SCHEDULER CONFIGURATION ---
    def scheduled_job_wrapper():
        with app.app_context():
            # --- SEED TICKERS ON APP START ---
            print("--- SEEDING TICKERS ---")
            seed_database()
            print("--- TICKERS SEEDED ---")
                    
            db_path = os.path.join(app.instance_path, 'financial_data.db')
            run_data_collection(db_path)
            
    scheduler = BackgroundScheduler(daemon=True, timezone=pytz.utc)
    scheduler.add_job(
        func=scheduled_job_wrapper, 
        trigger='cron', 
        hour=2, 
        id='daily_data_job',
        replace_existing=True
    )
    scheduler.start()

    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(id):
        return User.query.get(int(id))

    return app


def create_database(app):
    if not path.exists('website/' + DB_NAME):
        db.create_all(app=app)
        print('Created Database!')