from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from .models import User
from werkzeug.security import generate_password_hash, check_password_hash
from . import db, mail
from flask_login import login_user, login_required, logout_user, current_user
from flask_mail import Message # For creating email messages
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature # For tokens
import re
from flask import session

auth = Blueprint('auth', __name__)

# Helper function to get the token serializer
def get_token_serializer():
    # Uses your app's SECRET_KEY for signing. Keep SECRET_KEY secure!
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])

def validate_password_strength(password, confirm_password=None):
    """
    Validates password strength and optionally confirms it matches a confirmation.
    Returns None if valid, or an error message string if invalid.
    """
    if confirm_password is not None and password != confirm_password:
        return "Passwords don't match." # Specific error for mismatch handled differently in sign_up
    if len(password) < 12:
        return "Password must be at least 12 characters."
    if not re.search(r"[A-Z]", password):
        return 'Password must contain at least one uppercase letter.'
    if not re.search(r"[a-z]", password):
        return 'Password must contain at least one lowercase letter.'
    if not re.search(r"[^a-zA-Z0-9]", password): # Checks for at least one symbol
        return 'Password must contain at least one symbol (e.g., !@#$%).'
    return None # Password is valid

# --- Route to Request Password Reset ---
@auth.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password_request():
    if current_user.is_authenticated: # If user is already logged in, redirect them
        return redirect(url_for('views.home'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        
        if user:
            try:
                s = get_token_serializer()
                token = s.dumps(email, salt='password-reset-salt')
                reset_url = url_for('auth.reset_password_with_token', token=token, _external=True)
                
                # Create and send the email
                msg_title = "Password Reset Request for Your Quantarize Account"
                sender_name, sender_email = current_app.config['MAIL_DEFAULT_SENDER'] # Get tuple
                
                msg = Message(msg_title, 
                              sender=(sender_name, sender_email), # Use tuple for sender
                              recipients=[email])
                
                msg.body = f"""Hi {user.first_name},

We have received a request to set the password for your Quantarize account.
If this was you, please click the link below to choose a new password. This link will expire in 1 hour.

{reset_url}

If you did not request this password reset, please ignore this email. Your password will remain unchanged.

Thank you,
Justin 
Founder 
The Quantarize Team
"""
                mail.send(msg)
                flash('A password reset link has been sent to your email. Please check your inbox (and spam folder). It will expire in 1 hour.', 'info')
                return redirect(url_for('auth.login'))
            except Exception as e:
                current_app.logger.error(f"Password reset email sending failed for {email}: {e}")
                flash('An error occurred while trying to send the reset email. Please ensure your email is correct or try again later.', 'error')
                # Optionally, redirect to forgot_password_request to allow re-try
                return redirect(url_for('auth.forgot_password_request'))
        else:
            # User not found, show a generic message to avoid confirming email existence
            flash('If an account with that email exists, a reset link has been sent. Please check your inbox.', 'info')
            return redirect(url_for('auth.login')) # Redirect to login to avoid confirming non-existence

    return render_template("forgot_password_request.html", user=current_user)

# --- Route to Handle Password Reset with Token ---
@auth.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password_with_token(token):
    if current_user.is_authenticated:
        return redirect(url_for('views.home'))

    s = get_token_serializer()
    try:
        # Validate the token and get the email. Expires after 1 hour (3600 seconds).
        email = s.loads(token, salt='password-reset-salt', max_age=3600)
    except SignatureExpired:
        flash('The password reset link has expired. Please request a new one.', 'error')
        return redirect(url_for('auth.forgot_password_request'))
    except BadTimeSignature:
        flash('Invalid or tampered password reset link. Please request a new one.', 'error')
        return redirect(url_for('auth.forgot_password_request'))
    except Exception: # Catch any other errors from loads (e.g., malformed token)
        flash('Invalid password reset link.', 'error')
        return redirect(url_for('auth.forgot_password_request'))

    user = User.query.filter_by(email=email).first()
    if not user:
        # This case should ideally not be reached if token generation was tied to an existing user's email
        flash('User not found for this reset link. It might be invalid.', 'error')
        return redirect(url_for('auth.forgot_password_request'))

    if request.method == 'POST':
        password_one = request.form.get('password_one')
        password_two = request.form.get('password_two')
        
        password_error = validate_password_strength(password_one, password_two) 
        
        if password_error:
            return render_template("reset_password_form.html", token=token, user=current_user, password_error=password_error)
        else:
            user.password = generate_password_hash(password_one, method='pbkdf2:sha256:1000000')
            db.session.commit()
            flash('Your password has been successfully updated! You can now log in.', 'success')
            return redirect(url_for('auth.login'))

    return render_template("reset_password_form.html", token=token, user=current_user)

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        if user:
            if check_password_hash(user.password, password):
                flash('Logged in successfully', category='success')
                login_user(user, remember=True)
                return redirect(url_for('views.home'))
            else:
                flash('Incorrect password, try again.', category='error')
        else:
            flash('Email does not exist.', category='error')
            
    return render_template("login.html", user=current_user)


@auth.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


@auth.route('/sign-up', methods=['GET', 'POST'])
def sign_up():
    form_data_to_repopulate = {}
    password_specific_error = None # Initialize

    if request.method == 'POST':
        email = request.form.get('email')
        first_name = request.form.get('firstName')
        last_name = request.form.get('lastName')
        password_one = request.form.get('password1')
        password_two = request.form.get('password2')

        form_data_to_repopulate['email'] = email
        form_data_to_repopulate['first_name'] = first_name
        form_data_to_repopulate['last_name'] = last_name

        user = User.query.filter_by(email=email).first()
        validation_passed = True

        if user:
            flash('Email already exists.', category='error')
            validation_passed = False
        elif len(email) < 4:
            flash('Email must be greater than 3 characters.', category='error')
            validation_passed = False
        elif len(first_name) < 2:
            flash('First name must be greater than 1 character.', category='error')
            validation_passed = False
        elif len(last_name) < 2:
            flash('Last name must be greater than 1 character.', category='error')
            validation_passed = False
        elif password_one != password_two:
            # This is a clear mismatch, distinct from password strength.
            # Flashing it is fine, or you could set a specific confirm_password_error.
            # For now, keeping it as a general flash message.
            flash("Passwords don't match.", category='error')
            validation_passed = False
        # --- Start of specific password criteria checks ---
        # These will only be effectively checked if passwords_match or if password_one is validated independently.
        # The current elif structure means these are checked sequentially if previous conditions pass.
        else: # If passwords match, then check strength
            password_specific_error = validate_password_strength(password_one) # No need to pass password_two here
            if password_specific_error:
                validation_passed = False

        if validation_passed:
            new_user = User(email=email,
                            first_name=first_name,
                            last_name=last_name,
                            password=generate_password_hash(password_one, method='pbkdf2:sha256:1000000'))
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user, remember=True)
            flash('Account created!', category='success')
            return redirect(url_for('views.home'))

    # For GET requests, or for POST requests where validation failed:
    return render_template("sign_up.html",
                           user=current_user,
                           password_error=password_specific_error, # Pass the specific password error
                           **form_data_to_repopulate)