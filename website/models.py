from . import db
from flask_login import UserMixin
from sqlalchemy.sql import func
from sqlalchemy import desc

class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.String(10000))
    date = db.Column(db.DateTime(timezone=True), default=func.now())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))    
    

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True)
    password = db.Column(db.String(150))
    first_name = db.Column(db.String(150))
    last_name = db.Column(db.String(150))
    notes = db.relationship('Note', backref='author', lazy=True, order_by=desc(Note.date))
    valuation_logs = db.relationship('ValuationLog', backref='user', lazy=True)
    
    
class ValuationLog(db.Model):
    __tablename__ = 'valuation_log' # Optional: explicitly name the table
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ticker_symbol = db.Column(db.String(20), nullable=False) # Max length for typical tickers
    search_timestamp = db.Column(db.DateTime(timezone=True), default=func.now())
    # You could add more fields here in the future, e.g.:
    # assumptions_used = db.Column(db.Text) # JSON string of assumptions
    # calculated_iv_moderate = db.Column(db.Float)
    # discount_rate_used = db.Column(db.Float)

    def __repr__(self):
        return f"<ValuationLog id={self.id} user_id={self.user_id} ticker='{self.ticker_symbol}' timestamp='{self.search_timestamp}'>"